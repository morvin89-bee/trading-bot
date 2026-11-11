"""Metadata pair publik Indodax: batas minimum, presisi qty, dan fee.

Sumber: endpoint publik `https://indodax.com/api/pairs` (tanpa kunci API).
Arti field mengikuti dokumentasi resmi Indodax (`Public-RestAPI.md`):

- `quantity_increment`  : kelipatan qty terkecil yang diterima bursa;
- `trade_min_base_currency`    : minimum nominal IDR per order;
- `trade_min_traded_currency`  : minimum jumlah koin per order (dinamis);
- `trade_fee_percent`   : fee taker dalam PERSEN (0,2 = 0,2%).

Metadata disimpan di SQLite dengan cap waktu dan disegarkan paling banyak
sekali sehari. Percobaan yang GAGAL juga dibatasi `RETRY_S`: denyut bot
lima menit tidak boleh berarti percobaan jaringan tiap lima menit. `get`
TIDAK menulis dan TIDAK menebak: metadata hilang/basi mengembalikan `None`,
dan pemanggil (engine) wajib berhenti — bukan memakai angka lama atau
membulatkan tanpa batas bursa.
"""
import sqlite3
from dataclasses import dataclass

PAIRS_URL = "https://indodax.com/api/pairs"
MAX_AGE_S = 86400            # metadata lebih tua dari ini = basi -> halt
RETRY_S = 900                # percobaan gagal berikutnya paling cepat 15 menit


@dataclass(frozen=True)
class Meta:
    """Satu baris metadata pair yang sudah terverifikasi bentuknya."""
    pair: str
    quantity_increment: str  # disimpan apa adanya; Decimal(str(...)) saat dipakai
    min_base_idr: float
    min_traded: float
    fee_pct: float           # FRAKSI: 0,2% -> 0,002 (satuan proposal.FEE_PCT)
    fetched_ts: float


def _ensure_table(conn):
    """Buat tabel cache metadata bila belum ada (idempoten)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pair_meta (
            pair TEXT PRIMARY KEY,
            quantity_increment TEXT NOT NULL,
            min_base_idr REAL NOT NULL,
            min_traded REAL NOT NULL,
            fee_pct REAL NOT NULL,
            fetched_ts REAL NOT NULL
        )
        """
    )


def _ensure_state(conn):
    """Tabel satu baris untuk cap waktu percobaan terakhir (sukses/gagal)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pair_meta_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_attempt_ts REAL NOT NULL
        )
        """
    )


def _percobaan_terakhir(conn):
    try:
        row = conn.execute(
            "SELECT last_attempt_ts FROM pair_meta_state WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else float(row[0])


def _catat_percobaan(conn, now):
    conn.execute(
        "INSERT OR REPLACE INTO pair_meta_state (id, last_attempt_ts) "
        "VALUES (1, ?)", (float(now),))
    conn.commit()


def _parse(rows, now):
    """Ubah respons `pairs` menjadi `{pair: Meta}`; baris rusak dilewati."""
    out = {}
    for r in rows or []:
        pair = str(r.get("ticker_id") or "").strip()
        try:
            increment = r["quantity_increment"]
            min_base = float(r["trade_min_base_currency"])
            min_traded = float(r["trade_min_traded_currency"])
            fee_pct = float(r["trade_fee_percent"]) / 100.0
        except (KeyError, TypeError, ValueError):
            continue
        if (not pair or increment is None or float(increment) <= 0
                or min_base < 0 or min_traded < 0 or fee_pct < 0):
            continue
        out[pair] = Meta(pair, str(increment), min_base, min_traded,
                         fee_pct, float(now))
    return out


def _ambil():
    """Ambil metadata dari bursa (satu-satunya jaringan di modul ini)."""
    import requests
    resp = requests.get(PAIRS_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _refresh(conn, *, now, fetch=None):
    """Segarkan cache bila sudah basi; kembalikan jumlah pair yang ditulis.

    Idempoten: metadata yang masih segar tidak membuka jaringan. Percobaan
    GAGAL juga dicap waktunya SEBELUM jaringan, jadi kegagalan beruntun
    dibatasi `RETRY_S` dan tidak mengikuti denyut bot.
    """
    _ensure_table(conn)
    _ensure_state(conn)
    baris = conn.execute("SELECT MAX(fetched_ts) FROM pair_meta").fetchone()
    terakhir = baris[0] if baris else None
    if terakhir is not None and 0 <= float(now) - float(terakhir) < MAX_AGE_S:
        return 0
    percobaan = _percobaan_terakhir(conn)
    if (percobaan is not None
            and 0 <= float(now) - percobaan < RETRY_S):
        return 0
    _catat_percobaan(conn, now)
    data = _parse((fetch or _ambil)(), now)
    if not data:
        raise ValueError(
            "metadata pair kosong/tak terbaca — cache lama tidak diperbarui")
    for m in data.values():
        conn.execute(
            "INSERT OR REPLACE INTO pair_meta "
            "(pair, quantity_increment, min_base_idr, min_traded, fee_pct, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?, ?)",
            (m.pair, m.quantity_increment, m.min_base_idr, m.min_traded,
             m.fee_pct, m.fetched_ts))
    conn.commit()
    return len(data)


def refresh(conn, *, now, fetch=None):
    """Pembungkus `_refresh` (tes menyuntikkan `fetch` lewat nama ini)."""
    return _refresh(conn, now=now, fetch=fetch)


def _get(conn, pair, *, now, max_age_s=MAX_AGE_S):
    """Metadata `pair` yang usianya 0..`max_age_s`, atau `None`.

    Waktu cap di masa depan (jam mundur) juga ditolak — bukan "segar".
    Tabel belum ada = belum ada metadata, bukan kegagalan.
    """
    try:
        row = conn.execute(
            "SELECT quantity_increment, min_base_idr, min_traded, fee_pct, "
            "fetched_ts FROM pair_meta WHERE pair = ?", (pair,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    umur = float(now) - float(row[4])
    if not (0 <= umur <= max_age_s):
        return None
    return Meta(pair, str(row[0]), float(row[1]), float(row[2]), float(row[3]),
                float(row[4]))


def get(conn, pair, *, now, max_age_s=MAX_AGE_S):
    """Pembungkus `_get` (tes menyuntikkan metadata lewat nama ini)."""
    return _get(conn, pair, now=now, max_age_s=max_age_s)


def umur_tertua(conn, *, now):
    """Umur baris metadata TERTUA dalam detik; `None` bila belum ada.

    Dipakai pemanggil untuk memberi tahu pengguna HANYA ketika metadata
    benar-benar basi, bukan setiap kali percobaan penyegaran gagal.
    """
    try:
        row = conn.execute("SELECT MIN(fetched_ts) FROM pair_meta").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    return float(now) - float(row[0])
