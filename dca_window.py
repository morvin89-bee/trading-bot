"""Jendela DCA bulanan: DND, pengingat pasif, dan status 24 jam.

Aturan (spec §7.2-7.3):
- Konfirmasi DCA dikirim 09.00 lokal; jendela jawab 24 jam; lewat -> "missed".
- DND 23.00-07.00 lokal: batas awal inklusif, batas akhir eksklusif.
- Pengingat pasif pada 15.00 dan 21.00 (03.00 sengaja tidak ada — berada di DND).
  Pengingat hanya mengembalikan True; pengirimannya urusan pemanggil (Task 14):
  maksimal satu pesan per slot pengingat.

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

# Nilai resmi `dca_status`. Kolom ini HANYA boleh ditulis lewat fungsi modul ini
# (mark_pending / mark_confirmed / mark_missed / mark_batal) — dilarang keras
# menulisnya lewat UPDATE langsung. Nilai di luar daftar ini membuat dca_state
# gagal keras (ValueError), bukan diam-diam jatuh ke awaiting dan menulis
# catatan dca_missed palsu (review T-13.4).
STATUS_DCA = ("awaiting", "confirmed", "missed", "batal")


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

    Syarat: harus ada jendela pengiriman yang masih "awaiting" di bulan yang sama.
    Tanpa syarat itu fungsi ini NO-OP — arahnya sengaja: tombol Setuju dari
    keyboard basi yang mendarat sebelum pengiriman, atau jawaban yang mendarat di
    bulan lain dari jendelanya, tidak boleh mengarang bulan "confirmed" dan
    menghapus DCA bulan itu diam-diam (review T-13.3). Karena syaratnya "awaiting",
    status "missed" dan "batal" juga otomatis ditolak.
    """
    baris = _baris(conn)
    if not (baris[0] == _bulan(ts) and baris[4] == "awaiting"):
        return
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'confirmed' WHERE id = 1",
        (_bulan(ts),),
    )
    conn.commit()


def _catatan_missed_ada(conn, bulan):
    """True bila penanda audit dca_missed untuk `bulan` sudah ada di shadow_log.

    Guard memakai BARIS AUDIT, bukan status — lihat mark_missed (review T-13.2).
    """
    ada_tabel = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='proposals'"
    ).fetchone()[0]
    if not ada_tabel:
        return False
    return (
        conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE id = ?",
            (f"dca_missed_{bulan}",),
        ).fetchone()[0]
        > 0
    )


def mark_missed(conn, ts):
    """Tandai jendela DCA yang lewat dilewati, dan catat ke shadow_log tepat satu kali (F8).

    Dua prasyarat (review T-13.6) — selain itu NO-OP, tanpa tulisan apa pun:

    1. HARUS ADA JENDELA: `last_dca_date` tidak NULL. Tanpa jendela, menulis
       akan memalsukan bulan — persis pemalsuan yang dijaga mark_confirmed
       (T-13.3) dan mark_batal (T-13.5). Syarat ini TIDAK sebulan: jendela bisa
       melintasi batas bulan (dikirim hari terakhir, lewat hari pertama bulan
       berikutnya) dan saat lewat statusnya masih "awaiting", jadi jendela
       lintas-bulan tetap boleh ditandai.
    2. STATUS MASIH "awaiting" — menolak "confirmed" (persetujuan tidak boleh
       hilang diam-diam), "missed" (idempoten), dan "batal" (bulan yang sudah
       dibatalkan tidak boleh mendapat catatan dca_missed palsu).

    Sisa kecelakaan (proses mati di antara commit catatan dan commit status)
    masih memenuhi kedua prasyarat — statusnya memang "awaiting" — dan guard
    anti-ganda di sini berangkat dari BARIS AUDIT (`_catatan_missed_ada`), bukan
    dari status: pemanggilan berikutnya melewati catatan yang sudah ada lalu
    menyetel status — memulihkan diri, bukan mati IntegrityError permanen
    (review T-13.2).

    PEMANGGIL (Task 14) DILARANG menulis catatan shadow_log "dca_missed" sendiri —
    termasuk di bawah id lain; modul ini sudah mencatatnya tepat sekali.

    Transisi yang tak pernah diamati siklus apa pun (bot mati, lalu bulan
    berganti) hilang bersama bulannya — backfill di luar cakupan, sengaja
    (review Minor 4).

    Urutan tulisan: jejak audit dulu, baru commit status. Bila terputus di
    tengah, yang HILANG hanyalah commit statusnya (baris audit tetap tersimpan);
    kehilangan itu terdeteksi dan diperbaiki pada pemanggilan berikutnya — lihat
    paragraf sisa kecelakaan di atas. Yang tidak pernah terjadi di sini:
    catatan audit hilang diam-diam tanpa jejak.
    """
    baris = _baris(conn)
    if baris[0] is None:
        return  # tidak ada jendela — tidak boleh memalsukan bulan (T-13.6)
    if baris[4] != "awaiting":
        return  # confirmed / missed / batal tidak ditimpa dan tidak dicatat (T-13.6)
    bulan = _bulan(ts)
    if not _catatan_missed_ada(conn, bulan):
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


def mark_batal(conn, ts):
    """Catat tombol "Batal bulan ini" (spec L205): menyetel dca_status = 'batal'.

    TIDAK menulis catatan shadow_log apa pun — batal bukan penolakan dan bukan
    kelalaian, jadi tidak ada kejadian yang perlu dicatat. `dca_state`
    memperlakukan batal sebagai "none": tanpa sisa jendela, tanpa pengingat,
    tanpa catatan missed. Kontrak empat status `dca_state` tidak berubah.

    Dua prasyarat (review T-13.5) — selain itu NO-OP, tanpa tulisan apa pun:

    1. Baris BELUM SELESAI untuk bulan ini: menolak status "confirmed" atau
       "missed" — tombol basi atau tekanan ganda tidak boleh menghapus keputusan
       yang sudah tercatat (pengguna menyetujui, lalu Batal menimpa jadi "none"
       tanpa jejak sama sekali).
    2. Baris MEMEGANG BULAN LAIN: menolak bila `last_dca_date` bukan NULL dan
       bukan bulan `ts` — tombol dari bulan lain tidak boleh mengklaim bulan ini
       lewat penulisan `last_dca_date` (klaim = gerbang "jadwal baru bulan
       depan" Task 14), persis pemalsuan yang dijaga mark_confirmed (T-13.3).

    Pada baris kosong (tanpa keadaan) atau jendela "awaiting" bulan ini, batal
    berlaku: membatalkan jendela yang sedang menunggu memang menghentikan order
    bulan itu — arahnya menahan order, tidak pernah memerintahkannya. Inilah
    salah satu dari empat jalan menulis `dca_status` (lihat STATUS_DCA); tanpa
    fungsi ini Task 14 tergoda menulis kolomnya langsung, yang dilarang.
    """
    baris = _baris(conn)
    bulan = _bulan(ts)
    if baris[0] == bulan and baris[4] in ("confirmed", "missed"):
        return  # sudah selesai bulan ini — tombol basi tidak menghapus keputusan (T-13.5)
    if baris[0] not in (None, bulan):
        return  # baris memegang bulan lain — tombol lintas-bulan tidak berlaku (T-13.5)
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'batal' WHERE id = 1",
        (bulan,),
    )
    conn.commit()


def dca_state(conn, now_ts):
    """Status DCA bulan berjalan: ("none" | "awaiting" | "confirmed" | "missed", sisa_detik).

    Jendela 24 jam dihitung dari waktu pengiriman; batas akhir eksklusif — tepat
    24 jam sesudah pengiriman sudah "missed". Lewat batas, shadow_log dicatat
    tepat sekali pada siklus transisi (F8), bukan setiap siklus.
    `sisa_detik` hanya bermakna untuk "awaiting"; status lain selalu 0.0.
    Status "batal" (mark_batal) menghasilkan ("none", 0.0) — tanpa jendela,
    tanpa pengingat, tanpa catatan missed.

    Gagal keras, bukan diam-diam: `dca_status` di luar STATUS_DCA (penulisan
    langsung yang terlarang) → ValueError; `dca_sent_ts` NULL dengan bulan terisi
    → ValueError — data rusak tidak boleh ditebak (review T-13.4 & Minor 2).
    """
    baris = _baris(conn)
    if baris[0] != _bulan(now_ts):
        return ("none", 0.0)
    status = baris[4]
    if status not in STATUS_DCA:
        raise ValueError(
            f"dca_status tak dikenal: {status!r}; nilai yang boleh {STATUS_DCA}. "
            "Kolom ini hanya ditulis lewat fungsi modul dca_window — "
            "penulisan langsung dilarang (review T-13.4)."
        )
    if status == "confirmed":
        return ("confirmed", 0.0)
    if status == "missed":
        return ("missed", 0.0)
    if status == "batal":
        return ("none", 0.0)
    # status "awaiting" — waktu pengiriman selalu ikut tersimpan oleh mark_pending;
    # NULL di sini berarti data rusak, dan data rusak tidak boleh ditebak.
    kirim = baris[3]
    if kirim is None:
        raise ValueError(
            "dca_sent_ts kosong padahal bulan DCA terisi — data rusak; "
            "dca_window tidak pernah menebak waktu pengiriman (review Minor 2)."
        )
    batas = kirim + DCA_WINDOW_H * 3600
    if now_ts < batas:
        return ("awaiting", batas - now_ts)
    mark_missed(conn, now_ts)
    return ("missed", 0.0)


def should_remind(conn, now_ts):
    """True hanya bila jam lokal di REMINDER_HOURS, di luar DND, dan DCA masih awaiting.

    True sepanjang satu jam pengingat (mis. 15.00-15.59); pemanggil WAJIB
    mengirim maksimal satu pesan per slot — predikat ini tidak boleh menulis
    apa pun untuk mengurangi sendiri, jadi dedupe ada di tangan pemanggil
    (review T-13.1: HEARTBEAT_S 300 detik × 12 tick per jam tanpa dedupe =
    12 pesan identik, melanggar spec "satu pengingat ringan").

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
