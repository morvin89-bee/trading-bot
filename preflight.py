"""Preflight read-only sebelum go-live: `python -m preflight [--target X]`.

Pemeriksaan yang menentukan GO/NO-GO, semuanya TANPA menulis apa pun:

1. offset zona mesin harus +07:00 (WIB) — sama dengan penjaga di `main()`;
2. basis data bisa dibuka `mode=ro` dan skemanya sudah dimigrasikan;
3. metadata pair (`pair_meta`) untuk semua koin yang dilacak masih segar —
   engine membulatkan qty dari metadata ini dan halt bila hilang/basi;
4. izin kunci API dari `.env` (`canWithdraw` harus False, `canTrade` cocok
   dengan sakelar) — dinilai `safeguards.permissions_ok`;
5. selisih jam mesin dengan jam bursa masih di bawah toleransi;
6. status mode bayangan (hari ke-N) terhadap lantai `GO_LIVE_SHADOW_DAYS`;
7. jendela DCA bulan berjalan.

`--target live` (default) menuntut lantai hari bayangan. `--target bayangan`
memeriksa "boleh mulai mode bayangan?": lantai hari TIDAK dipersyaratkan,
tetapi sakelar eksekusi harus tetap mati.

Tiga hasil per pemeriksaan: `True` = lolos, `False` = dievaluasi dan gagal,
`None` = TIDAK BISA DIEVALUASI (DB tak terbuka, izin tak terbaca, jam bursa
gagal dibaca). Kode keluar: 0 = GO, 1 = NO-GO (ada kegagalan yang bisa
dievaluasi; menang atas 2), 2 = tidak bisa dievaluasi.

Basis data dibuka `file:...?mode=ro`: tidak ada `CREATE`, `ALTER`, atau
`INSERT`. `dca_window.dca_state` SENGAJA tidak dipakai karena transisi
"awaiting lewat 24 jam" menulis `mark_missed`; jendela dihitung ulang di
sini dengan aturan yang sama, tanpa transisi.

Tidak ada order dan tidak ada transport bursa di modul ini. Satu-satunya
jaringan adalah pembacaan jam bursa (`ambil_waktu_bursa`), yang selalu bisa
disuntik pada tes. Keluaran hanya memuat hasil pemeriksaan — tidak pernah
nilai kunci API atau rahasia `.env`.
"""
import argparse
import sqlite3
import time

import dca_window
import indodax_transport
import main_loop
import pair_meta
import portfolio_store
import rules
import safeguards

# Selisih jam mesin vs bursa yang masih dianggap aman. `_v2_get` mengirim
# recvWindow 15 detik; 5 detik menyisakan ruang untuk latensi jaringan.
TOLERANSI_JAM_DETIK = 5.0

_TABEL_WAJIB = ("trades", "bot_state", "bot_state_audit", "proposals")
_KOLOM_BOT_STATE = (
    "last_dca_date", "v1_enabled", "shadow_day", "dca_sent_ts", "dca_status",
    "shadow_checked_day", "telegram_offset",
)
_KOLOM_PROPOSALS = (
    "id", "ts", "day", "kind", "pair", "amount_idr", "price_ref", "rule",
    "outcome", "note", "qty", "reserve_after", "window_s", "level", "basis",
    "revisi", "client_order_id",
)

TARGET = ("live", "bayangan")


def _buka_ro(db_path):
    """Buka basis data TANPA hak tulis; berkas yang belum ada = gagal."""
    jalur = str(db_path).replace("\\", "/")
    return sqlite3.connect(f"file:{jalur}?mode=ro", uri=True)


def _tabel(conn):
    return {b[0] for b in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _kolom(conn, tabel):
    return {b[1] for b in conn.execute(f"PRAGMA table_info({tabel})")}


def _periksa_db(conn):
    """Skema yang dibutuhkan bot sudah ada dan sudah dimigrasikan."""
    hasil = []
    tabel = _tabel(conn)
    kurang = [t for t in _TABEL_WAJIB if t not in tabel]
    if kurang:
        hasil.append((False, "db", f"tabel wajib belum ada: {', '.join(kurang)}"))
    else:
        hasil.append((True, "db", "tabel wajib ada: " + ", ".join(_TABEL_WAJIB)))
    for nama, wajib in (("bot_state", _KOLOM_BOT_STATE),
                        ("proposals", _KOLOM_PROPOSALS)):
        if nama not in tabel:
            continue
        kurang = [k for k in wajib if k not in _kolom(conn, nama)]
        if kurang:
            hasil.append((
                False, f"migrasi {nama}",
                f"kolom belum dimigrasikan: {', '.join(kurang)}"))
        else:
            hasil.append((True, f"migrasi {nama}", "semua kolom ada"))
    return hasil


def _periksa_meta(conn, now):
    """Metadata pair yang DIPAKAI strategi masih segar (read-only).

    Engine membulatkan qty dari metadata ini; hilang/basi berarti order
    dihalt. Preflight harus menolak GO pada keadaan yang pasti halt itu.
    Hanya pair di `rules.DEFAULT_TARGET` yang dituntut (putusan pemilik
    2026-11-15, opsi A): pair di luar target tidak pernah menghasilkan
    usulan, jadi metadatanya tidak boleh memblokir go-live.
    """
    pairs = sorted(p for p in rules.DEFAULT_TARGET if p != "IDR")
    basi = [pair for pair in pairs
            if pair_meta._get(conn, pair, now=now) is None]
    if basi:
        return (False, "metadata pair",
                f"basi/hilang: {', '.join(basi)} "
                f"(batas {pair_meta.MAX_AGE_S:.0f} detik)")
    return (True, "metadata pair", "segar untuk: " + ", ".join(pairs))


def _baca_state(conn):
    """Baris `bot_state` (read-only); `{}` bila tabelnya belum ada."""
    try:
        baris = conn.execute(
            "SELECT v1_enabled, shadow_day, last_dca_date, dca_sent_ts, "
            "dca_status FROM bot_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return {}
    if baris is None:
        return {}
    return {
        "v1_enabled": bool(baris[0]), "shadow_day": int(baris[1] or 0),
        "last_dca_date": baris[2], "dca_sent_ts": baris[3],
        "dca_status": baris[4],
    }


def _dca_baca_saja(state, now):
    """Jendela DCA bulan ini TANPA transisi `mark_missed`.

    Mengembalikan `(status, sisa_detik, catatan)`; status `"rusak"` berarti
    data tidak bisa dipercaya (bukan tebakan).
    """
    if not state or state["last_dca_date"] != dca_window._bulan(now):
        return ("none", 0.0, "belum ada jendela DCA bulan ini")
    status = state["dca_status"]
    if status not in dca_window.STATUS_DCA:
        return ("rusak", 0.0, f"dca_status tak dikenal: {status!r}")
    if status in ("confirmed", "missed"):
        return (status, 0.0, f"status bulan ini: {status}")
    if status == "batal":
        return ("none", 0.0, "DCA bulan ini dibatalkan (batal)")
    kirim = state["dca_sent_ts"]
    if kirim is None:
        return ("rusak", 0.0,
                "dca_sent_ts kosong padahal bulan DCA terisi")
    batas = kirim + dca_window.DCA_WINDOW_H * 3600
    if now < batas:
        return ("awaiting", batas - now,
                f"menunggu jawaban, sisa {max(0.0, batas - now) / 3600:.1f} jam")
    return ("lewat", 0.0,
            "jendela 24 jam sudah lewat; bot akan menandainya missed")


def _izin_dari_env():
    """Izin kunci dari deklarasi `.env` — Indodax tidak mengeksposnya."""
    return (indodax_transport._env_bool("INDODAX_CAN_TRADE"),
            indodax_transport._env_bool("INDODAX_CAN_WITHDRAW"))


def _waktu_bursa():
    """Jam server Indodax dalam epoch detik (satu-satunya jaringan di sini)."""
    import requests
    resp = requests.get("https://indodax.com/api/server_time", timeout=10)
    resp.raise_for_status()
    return float(resp.json()["server_time"]) / 1000.0


def periksa(*, db_path=None, sekarang=None, baca_izin=None,
            ambil_waktu_bursa=None, target="live"):
    """Jalankan semua pemeriksaan; kembalikan daftar `(ok, nama, pesan)`.

    `ok` tiga keadaan: `True` lolos, `False` gagal (bisa dievaluasi), `None`
    tidak bisa dievaluasi. `target` = `"live"` (default) atau `"bayangan"`.
    """
    if target not in TARGET:
        raise ValueError(
            f"target tidak dikenal: {target!r} (pilih: {', '.join(TARGET)})")
    sekarang = time.time if sekarang is None else sekarang
    baca_izin = _izin_dari_env if baca_izin is None else baca_izin
    ambil_waktu_bursa = (_waktu_bursa if ambil_waktu_bursa is None
                         else ambil_waktu_bursa)
    db_path = portfolio_store.DB_FILE if db_path is None else db_path
    now = sekarang()
    hasil = []

    offset = main_loop._offset_zona_lokal(now)
    hasil.append((
        offset == main_loop.ZONA_OFFSET_DETIK, "zona waktu",
        f"offset mesin {main_loop._teks_offset(offset)}; "
        "wajib +07:00 (WIB)"))

    conn = None
    state = None
    try:
        conn = _buka_ro(db_path)
        hasil.append((True, "db", f"terbuka read-only: {db_path}"))
        hasil.extend(_periksa_db(conn))
        hasil.append(_periksa_meta(conn, now))
        state = _baca_state(conn)
    except sqlite3.Error as e:
        hasil.append((None, "db", f"tidak bisa dibuka read-only: {e}"))
    finally:
        if conn is not None:
            conn.close()

    switch_on = None if state is None else bool(state.get("v1_enabled"))
    can_trade, can_withdraw = baca_izin()
    if can_trade is None or can_withdraw is None:
        hasil.append((
            None, "izin kunci",
            "izin API tidak terbaca (canTrade/canWithdraw tidak diketahui)"))
    else:
        alasan = safeguards.permissions_ok(
            can_trade, can_withdraw, switch_on=bool(switch_on))
        hasil.append((
            alasan is None, "izin kunci",
            alasan or f"canTrade={can_trade}, canWithdraw={can_withdraw} "
                      f"(sakelar {'nyala' if switch_on else 'mati'})"))

    try:
        selisih = abs(float(now) - float(ambil_waktu_bursa()))
        hasil.append((
            selisih <= TOLERANSI_JAM_DETIK, "jam bursa",
            f"selisih {selisih:.1f} detik (toleransi "
            f"{TOLERANSI_JAM_DETIK:.0f} detik)"))
    except Exception as e:
        hasil.append((None, "jam bursa", f"jam bursa tidak terbaca: {e}"))

    if state is None:
        hasil.append((None, "mode", "status mode tidak terbaca (bot_state)"))
    elif target == "bayangan":
        if switch_on:
            hasil.append((
                False, "mode",
                "sakelar eksekusi NYALA (v1_enabled=1) — bukan target "
                "bayangan; matikan dulu"))
        else:
            hasil.append((
                True, "mode",
                f"target bayangan, hari ke-{state.get('shadow_day', 0)}; "
                f"lantai {main_loop.GO_LIVE_SHADOW_DAYS} hari tidak "
                "dipersyaratkan untuk bayangan"))
    else:
        hari = state.get("shadow_day", 0)
        if switch_on:
            hasil.append((True, "mode",
                          "sakelar eksekusi NYALA (v1_enabled=1)"))
        else:
            hasil.append((
                hari >= main_loop.GO_LIVE_SHADOW_DAYS, "mode",
                f"mode bayangan, hari ke-{hari} dari lantai "
                f"{main_loop.GO_LIVE_SHADOW_DAYS}"))

    if state is None:
        hasil.append((None, "jendela DCA",
                      "tidak bisa dievaluasi: bot_state tidak terbaca"))
    else:
        status, _sisa, catatan = _dca_baca_saja(state, now)
        hasil.append((status != "rusak", "jendela DCA", catatan))
    return hasil


def main(*, db_path=None, sekarang=None, baca_izin=None,
         ambil_waktu_bursa=None, target="live"):
    """Cetak hasil preflight; 0 = GO, 1 = NO-GO, 2 = tidak bisa dievaluasi."""
    hasil = periksa(db_path=db_path, sekarang=sekarang, baca_izin=baca_izin,
                    ambil_waktu_bursa=ambil_waktu_bursa, target=target)
    print(f"Preflight go-live (read-only, tidak ada order; target: {target})")
    label = {True: "OK", False: "GAGAL", None: "TAK TERBACA"}
    for ok, nama, pesan in hasil:
        print(f"  [{label[ok]}] {nama}: {pesan}")
    gagal = [f"{nama}: {pesan}" for ok, nama, pesan in hasil if ok is False]
    if gagal:
        print("NO-GO — " + "; ".join(gagal))
        return 1
    tidak = [f"{nama}: {pesan}" for ok, nama, pesan in hasil if ok is None]
    if tidak:
        print("TIDAK BISA DIEVALUASI — " + "; ".join(tidak))
        return 2
    print("GO — semua pemeriksaan lolos")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Preflight read-only sebelum go-live.")
    parser.add_argument("--target", choices=TARGET, default="live",
                        help="live (default) menuntut lantai hari bayangan; "
                             "bayangan tidak")
    args = parser.parse_args()
    raise SystemExit(main(target=args.target))
