"""Jendela DCA bulanan: DND, pengingat pasif, dan status 24 jam.

Aturan (spec §7.2-7.3):
- Konfirmasi DCA dikirim 09.00 lokal; jendela jawab 24 jam; lewat -> "missed".
- DND 23.00-07.00 lokal: batas awal inklusif, batas akhir eksklusif.
- Pengingat pasif pada 15.00 dan 21.00 (03.00 sengaja tidak ada — berada di DND).
  Pengingat hanya mengembalikan True; pengirimannya urusan pemanggil (Task 14).

Semua perhitungan jam memakai time.localtime — memakai gmtime akan membuat
setiap batas jam salah diam-diam.

Kepemilikan skema state (Ruling R-G, progress.md): tabel `bot_state` dibuat di
sini oleh `ensure_bot_state` — bootstrap, karena kebutuhan pertama memang muncul
di Task 13. Task 14 WAJIB memakai tabel yang sama lewat `load_state`.

Modul ini tidak pernah membuka jaringan, tidak pernah mengirim apa pun, tidak
menyentuh kunci dagang, dan tidak pernah menempatkan order.
"""
import time
import types

import shadow_log

DCA_SEND_HOUR = 9
DCA_WINDOW_H = 24
DND = (23, 7)
REMINDER_HOURS = (15, 21)  # 03.00 sengaja tidak ada: berada di dalam DND


def in_dnd(now_ts):
    """True bila jam lokal berada di jendela DND.

    Batas awal inklusif, batas akhir eksklusif: 23.00 masuk, 07.00 keluar.
    """
    jam = time.localtime(now_ts).tm_hour
    mulai, akhir = DND
    if mulai <= akhir:
        return mulai <= jam < akhir
    return jam >= mulai or jam < akhir


def ensure_bot_state(conn):
    """Buat tabel `bot_state` beserta baris tunggalnya — idempoten (bootstrap, R-G).

    Satu baris (id = 1), satu pembuat. Task 14 WAJIB memakai tabel yang sama lewat
    load_state(conn) — dilarang membuat tabel atau pembuat state kedua, supaya
    skema tidak pernah bercabang dua bentuk.

    Kolom sesuai R-G: `last_dca_date` (TEXT, boleh NULL), `v1_enabled` (INTEGER
    0/1, default 0), `shadow_day` (INTEGER, default 0). Dua kolom terakhir,
    `dca_sent_ts` dan `dca_status`, adalah kerja jendela DCA: tanpanya
    `mark_pending` tidak punya tempat menyimpan waktu pengiriman dan `dca_state`
    tidak bisa membedakan awaiting/confirmed/missed. `total_disetor` dan
    `reserve_buy_enabled` TIDAK disimpan di sini (R-G): yang pertama sudah
    tersedia dari snapshots.contributions_idr, yang kedua berisiko ganda dengan
    v1_enabled.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_dca_date TEXT,
            v1_enabled INTEGER NOT NULL DEFAULT 0,
            shadow_day INTEGER NOT NULL DEFAULT 0,
            dca_sent_ts REAL,
            dca_status TEXT
        )
        """
    )
    conn.execute("INSERT OR IGNORE INTO bot_state (id) VALUES (1)")
    conn.commit()


def _bulan(ts):
    """Kunci bulan "YYYY-MM" dihitung dari waktu lokal."""
    return time.strftime("%Y-%m", time.localtime(ts))


def _baris(conn):
    """Baca baris state tunggal (dibuat lebih dulu bila belum ada)."""
    ensure_bot_state(conn)
    return conn.execute(
        "SELECT last_dca_date, v1_enabled, shadow_day, dca_sent_ts, dca_status "
        "FROM bot_state WHERE id = 1"
    ).fetchone()


def mark_pending(conn, ts):
    """Mulai jendela DCA: konfirmasi dianggap dikirim pada `ts` (waktu lokal)."""
    ensure_bot_state(conn)
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_sent_ts = ?, "
        "dca_status = 'awaiting' WHERE id = 1",
        (_bulan(ts), ts),
    )
    conn.commit()


def mark_confirmed(conn, ts):
    """Catat jawaban "Setuju" untuk DCA bulan ini — dipanggil Task 14 saat tombol ditekan.

    Tidak pernah menimpa status "missed": lewat jendela 24 jam DCA tidak dikejar
    (spec §7.2), jadi jawaban telat harus tetap "missed".
    """
    baris = _baris(conn)
    if baris[0] == _bulan(ts) and baris[4] == "missed":
        return
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'confirmed' WHERE id = 1",
        (_bulan(ts),),
    )
    conn.commit()


def mark_missed(conn, ts):
    """Tandai DCA bulan ini dilewati, dan catat ke shadow_log tepat satu kali (F8).

    Jejak audit ditulis lebih dulu, baru statusnya: bila terputus di tengah,
    kegagalan berikutnya nyaring (meledak), bukan catatan hilang diam-diam.
    Panggilan ganda di bulan yang sama TIDAK menulis catatan kedua.
    """
    ensure_bot_state(conn)
    bulan = _bulan(ts)
    baris = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    sudah_tercatat = baris[0] == bulan and baris[1] == "missed"
    if not sudah_tercatat:
        # Penanda audit tanpa angka yang dikarang: pair/nominal/harga tidak
        # diketahui karena memang tidak ada usulan yang pernah dibuat -> NULL.
        penanda = types.SimpleNamespace(
            id=f"dca_missed_{bulan}",
            created_ts=ts,
            kind="DCA",
            pair=None,
            amount_idr=None,
            price_ref=None,
            rule="DCA bulanan",
        )
        shadow_log.record(
            conn,
            penanda,
            "dca_missed",
            note="DCA bulan ini dilewati (tidak dibalas dalam 24 jam)",
        )
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'missed' WHERE id = 1",
        (bulan,),
    )
    conn.commit()


def dca_state(conn, now_ts):
    """Status DCA bulan berjalan: ("none" | "awaiting" | "confirmed" | "missed", sisa_detik).

    Jendela 24 jam dihitung dari waktu pengiriman; batas akhir eksklusif — tepat
    24 jam sesudah pengiriman sudah "missed". Lewat batas, shadow_log dicatat
    tepat sekali pada siklus transisi (F8), bukan setiap siklus.
    `sisa_detik` hanya bermakna untuk "awaiting"; status lain selalu 0.0.
    """
    baris = _baris(conn)
    if baris[0] != _bulan(now_ts):
        return ("none", 0.0)
    if baris[4] == "confirmed":
        return ("confirmed", 0.0)
    if baris[4] == "missed":
        return ("missed", 0.0)
    # status "awaiting" — waktu pengiriman selalu ikut tersimpan oleh mark_pending;
    # NULL di sini berarti data rusak, dan data rusak tidak boleh ditebak.
    kirim = baris[3]
    batas = kirim + DCA_WINDOW_H * 3600
    if now_ts < batas:
        return ("awaiting", batas - now_ts)
    mark_missed(conn, now_ts)
    return ("missed", 0.0)


def should_remind(conn, now_ts):
    """True hanya bila jam lokal di REMINDER_HOURS, di luar DND, dan DCA masih awaiting.

    Pengingat PASIF: fungsi ini tidak pernah mengubah jawaban yang tersimpan,
    tidak pernah menulis apa pun selain catatan "missed" yang wajib (F8), dan
    tidak pernah mengirim apa pun — pengiriman satu baris urusan pemanggil.
    """
    jam = time.localtime(now_ts).tm_hour
    if jam not in REMINDER_HOURS:
        return False
    if in_dnd(now_ts):
        return False
    return dca_state(conn, now_ts)[0] == "awaiting"
