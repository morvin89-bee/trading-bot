"""Jendela DCA bulanan: DND, pengingat pasif, dan status 24 jam.

Aturan (spec §7.2-7.3):
- Konfirmasi DCA dikirim 09.00 lokal; jendela jawab 24 jam; lewat -> "missed".
- DND 23.00-07.00 lokal: batas awal inklusif, batas akhir eksklusif.
- Pengingat pasif pada 15.00 dan 21.00 (03.00 sengaja tidak ada — berada di DND).
  Pengingat hanya mengembalikan True; pengirimannya urusan pemanggil (Task 14):
  maksimal satu pesan per slot pengingat.

Semua perhitungan jam memakai zona eksplisit `Asia/Jakarta` (WIB), BUKAN
`time.localtime` yang ikut pengaturan zona mesin. Bot yang pindah VPS dengan
zona UTC tidak boleh diam-diam menggeser jam kirim 09.00, DND, dan batas 21.00.
Modul lain (`main_loop`, `engine`) masih memakai `time.localtime`; mesin
produksi WAJIB WIB — lihat `docs/GOLIVE_CHECKLIST.md`.

Kepemilikan skema state (Ruling R-G, progress.md): tabel `bot_state` dibuat di
sini oleh `ensure_bot_state` — bootstrap, karena kebutuhan pertama memang muncul
di Task 13. Task 14 WAJIB memakai tabel yang sama lewat `load_state`.

Modul ini tidak pernah membuka jaringan, tidak pernah mengirim apa pun, tidak
menyentuh kunci dagang, dan tidak pernah menempatkan order.
"""
import sqlite3
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import shadow_log

# Zona eksplisit: batas jam DCA tidak boleh bergantung pada setelan mesin.
ZONA_LOKAL = ZoneInfo("Asia/Jakarta")


def _lokal(ts):
    """Waktu WIB untuk `ts` — satu-satunya sumber jam modul ini."""
    return datetime.fromtimestamp(ts, ZONA_LOKAL)

DCA_SEND_HOUR = 9
DCA_WINDOW_H = 24
DND = (23, 7)
REMINDER_HOURS = (15, 21)  # 03.00 sengaja tidak ada: berada di dalam DND

# Nilai resmi `dca_status`. Kolom ini HANYA boleh ditulis lewat fungsi modul ini
# (mark_pending / mark_confirmed / mark_missed / mark_batal / mark_skip) — dilarang keras
# menulisnya lewat UPDATE langsung. Nilai di luar daftar ini membuat dca_state
# gagal keras (ValueError), bukan diam-diam jatuh ke awaiting dan menulis
# catatan dca_missed palsu (review T-13.4).
STATUS_DCA = ("awaiting", "confirmed", "missed", "batal")


def in_dnd(now_ts):
    """True bila jam lokal berada di jendela DND.

    Batas awal inklusif, batas akhir eksklusif: 23.00 masuk, 07.00 keluar.
    """
    jam = _lokal(now_ts).hour
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
    tidak bisa membedakan awaiting/confirmed/missed. `shadow_checked_day` (TEXT,
    boleh NULL) adalah kerja Task 14: penanda hari lokal yang sudah dihitung,
    tanpa penanda itu `shadow_day` naik setiap denyut jantung dan sakelar
    menyala dalam hitungan jam, bukan 7 hari (spec §9).
    `telegram_offset` (INTEGER, default 0) adalah posisi `getUpdates` terakhir:
    tanpa kolom ini restart membuat update lama diproses ulang.
    `denyut_ok_ts` (REAL) dan `galat_terakhir` (TEXT) adalah kerja 2026-11-16:
    kapan terakhir satu denyut penuh berhasil, dan galat terakhir yang
    menggagalkannya — tanpa keduanya bot bisa mati berjam-jam tanpa jejak
    yang terbaca di dashboard maupun preflight.
    `total_disetor` dan
    `reserve_buy_enabled` TIDAK disimpan di sini (R-G): yang pertama sudah
    tersedia dari snapshots.contributions_idr, yang kedua berisiko ganda dengan
    v1_enabled.

    CATATAN TRANSAKSI: fungsi ini MENULIS dan COMMIT (bootstrap), walau bisa
    terpanggil dari jalur baca. Pemanggil tidak boleh menyandarkan tulisan
    yang belum di-commit pada koneksi yang sama lalu berharap rollback.
    """
    # `sqlite3.Row` supaya pembacaan memakai NAMA kolom, bukan indeks yang
    # bergeser diam-diam saat skema bertambah. Row tetap mendukung `[0]`.
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_dca_date TEXT,
            v1_enabled INTEGER NOT NULL DEFAULT 0,
            shadow_day INTEGER NOT NULL DEFAULT 0,
            dca_sent_ts REAL,
            dca_status TEXT,
            shadow_checked_day TEXT,
            telegram_offset INTEGER NOT NULL DEFAULT 0,
            status_terakhir_hari TEXT,
            sapa_terakhir_ts REAL,
            denyut_ok_ts REAL,
            galat_terakhir TEXT
        )
        """
    )
    # Basis data yang dibuat sebelum Task 14 tidak punya `shadow_checked_day`.
    # Penambahan kolom harus idempoten: dicek dulu lewat PRAGMA, baru ditambah
    # kalau belum ada — `ALTER TABLE` kedua akan gagal dan merusak pemanggil.
    # `bot_state` adalah tabel milik proyek ini sendiri, bukan data warisan
    # produksi, jadi larangan R-G (dilarang ALTER pada tabel warisan) tidak berlaku
    # di sini. Tabel warisan `portfolio_store` tetap tidak disentuh.
    # Indeks 1 = nama kolom (PRAGMA tidak memakai row_factory); nama dibaca
    # eksplisit supaya tidak bergantung pada urutan kolom PRAGMA.
    kolom = [
        baris["name"] for baris in conn.execute("PRAGMA table_info(bot_state)")
    ]
    if "shadow_checked_day" not in kolom:
        conn.execute("ALTER TABLE bot_state ADD COLUMN shadow_checked_day TEXT")
    # Basis data lama juga belum punya `telegram_offset`: tanpa migrasi ini
    # pembacaan offset setelah restart gagal keras (OperationalError).
    if "telegram_offset" not in kolom:
        conn.execute(
            "ALTER TABLE bot_state ADD COLUMN telegram_offset "
            "INTEGER NOT NULL DEFAULT 0"
        )
    # Penanda status harian dan sapa awal disimpan di DB, bukan di memori:
    # restart/crash-loop tidak boleh mengirim ulang pesan yang sama.
    if "status_terakhir_hari" not in kolom:
        conn.execute(
            "ALTER TABLE bot_state ADD COLUMN status_terakhir_hari TEXT")
    if "sapa_terakhir_ts" not in kolom:
        conn.execute("ALTER TABLE bot_state ADD COLUMN sapa_terakhir_ts REAL")
    # "Kapan terakhir bot benar-benar bekerja?" (2026-11-16): tanpa dua kolom
    # ini, bot bisa gagal berjam-jam dan tidak ada satu angka pun yang
    # menjawabnya — dashboard tampak normal, preflight tetap GO.
    if "denyut_ok_ts" not in kolom:
        conn.execute("ALTER TABLE bot_state ADD COLUMN denyut_ok_ts REAL")
    if "galat_terakhir" not in kolom:
        conn.execute("ALTER TABLE bot_state ADD COLUMN galat_terakhir TEXT")
    conn.execute("INSERT OR IGNORE INTO bot_state (id) VALUES (1)")
    # Jejak audit sakelar: siapa mengubah `v1_enabled`/`shadow_day`, kapan,
    # dan dari nilai berapa ke berapa. Ditulis `set_shadow_day` dan
    # `aktifkan_live` dalam transaksi yang SAMA dengan update-nya, supaya
    # tidak pernah ada perubahan sakelar tanpa jejak. Dibuat di sini karena
    # `bot_state` adalah milik modul ini (satu modul, satu skema).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state_audit (
            id INTEGER PRIMARY KEY,
            ts REAL NOT NULL,
            kolom TEXT NOT NULL,
            nilai_lama TEXT,
            nilai_baru TEXT,
            sumber TEXT NOT NULL
        )
        """
    )
    conn.commit()


def catat_audit_sakelar(conn, *, ts, sumber, lama, baru):
    """Catat perubahan kolom sakelar — satu baris per kolom yang BERUBAH.

    `lama`/`baru` = tuple `(v1_enabled, shadow_day)` SEBELUM/SESUDAH update.
    Nilai yang tidak berubah tidak dicatat: jejak audit adalah daftar
    perubahan, bukan log setiap panggilan.

    CATATAN TRANSAKSI: fungsi ini TIDAK commit. Pemanggil wajib menulis
    update sakelar dan baris audit ini lalu commit sekali, supaya keduanya
    tidak bisa terpisah.
    """
    for kolom, a, b in zip(("v1_enabled", "shadow_day"), lama, baru):
        if a == b:
            continue
        conn.execute(
            "INSERT INTO bot_state_audit "
            "(ts, kolom, nilai_lama, nilai_baru, sumber) VALUES (?, ?, ?, ?, ?)",
            (ts, kolom, None if a is None else str(a),
             None if b is None else str(b), sumber),
        )


def _bulan(ts):
    """Kunci bulan "YYYY-MM" dihitung dari waktu lokal."""
    return _lokal(ts).strftime("%Y-%m")


def _baris(conn):
    """Baca baris state tunggal; bootstrap HANYA bila tabel/baris belum ada.

    Jalur baca yang normal tidak menulis dan tidak commit — `ensure_bot_state`
    (yang ber-commit karena bootstrap) hanya dipanggil saat tabel belum ada.
    """
    conn.row_factory = sqlite3.Row
    kueri = (
        "SELECT last_dca_date, v1_enabled, shadow_day, dca_sent_ts, dca_status "
        "FROM bot_state WHERE id = 1"
    )
    try:
        baris = conn.execute(kueri).fetchone()
    except sqlite3.OperationalError:   # tabel belum pernah dibuat
        baris = None
    if baris is None:
        ensure_bot_state(conn)
        baris = conn.execute(kueri).fetchone()
    return baris


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

    Mengembalikan True bila konfirmasi diterapkan, False bila ditolak (review
    Minor 4) — pemanggil (Task 14) bisa memakainya untuk memberi tahu pengguna
    bahwa jawabannya tidak berlaku.
    """
    baris = _baris(conn)
    if not (baris["last_dca_date"] == _bulan(ts)
            and baris["dca_status"] == "awaiting"):
        return False
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'confirmed' WHERE id = 1",
        (_bulan(ts),),
    )
    conn.commit()
    return True


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

    Tiga prasyarat (review T-13.6 & T-13.7) — selain itu NO-OP (mengembalikan
    False), tanpa tulisan apa pun:

    1. HARUS ADA JENDELA: `last_dca_date` tidak NULL. Tanpa jendela, menulis
       akan memalsukan bulan — persis pemalsuan yang dijaga mark_confirmed
       (T-13.3) dan mark_batal (T-13.5). Syarat ini TIDAK sebulan: jendela bisa
       melintasi batas bulan (dikirim hari terakhir, lewat hari pertama bulan
       berikutnya) dan saat lewat statusnya masih "awaiting", jadi jendela
       lintas-bulan tetap boleh ditandai.
    2. STATUS MASIH "awaiting" — menolak "confirmed" (persetujuan tidak boleh
       hilang diam-diam), "missed" (idempoten), dan "batal" (bulan yang sudah
       dibatalkan tidak boleh mendapat catatan dca_missed palsu).
    3. WAKTU SUDAH LEWAT: `dca_sent_ts` tidak NULL dan `ts >= dca_sent_ts + 24
       jam`. PEMANGGIL (Task 14) DILARANG memanggil mark_missed sebelum jendela
       24 jam benar-benar lewat: menulis catatan "tidak dibalas dalam 24 jam"
       saat sisa jendela masih ada adalah PEMALSUAN ANGKA — dan baris itu masuk
       bahan tinjauan spec §12.2. Fungsi ini menolaknya sendiri (review T-13.7),
       tetapi disiplin pemanggil tetap wajib, seperti larangan menulis catatan
       shadow_log sendiri di bawah.

    Mengembalikan True bila dicatat, False bila ditolak salah satu prasyarat
    (review Minor 4) — pemanggil (Task 14) bisa memakainya untuk memberi tahu
    pengguna.

    Sisa kecelakaan (proses mati di antara commit catatan dan commit status)
    masih memenuhi ketiga prasyarat — statusnya memang "awaiting", jendelanya
    sudah lewat — dan guard anti-ganda di sini berangkat dari BARIS AUDIT
    (`_catatan_missed_ada`), bukan dari status: pemanggilan berikutnya melewati
    catatan yang sudah ada lalu menyetel status — memulihkan diri, bukan mati
    IntegrityError permanen (review T-13.2). Pemulihan ini idempoten HANYA
    dalam bulan yang sama: guard memakai `_bulan(ts)`, sedangkan baris yang
    selamat ter-key pada bulan lama — percobaan rekaman lintas bulan justru
    akan menulis baris dca_missed KEDUA di bawah id bulan baru. Lintas-bulan
    di luar cakupan, sengaja (review Minor 3 putaran ini).

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
    if baris["last_dca_date"] is None:
        return False  # tidak ada jendela — tidak boleh memalsukan bulan (T-13.6)
    if baris["dca_status"] != "awaiting":
        return False  # confirmed / missed / batal tidak ditimpa dan tidak dicatat (T-13.6)
    if (baris["dca_sent_ts"] is None
            or ts < baris["dca_sent_ts"] + DCA_WINDOW_H * 3600):
        return False  # jendela belum lewat — "tidak dibalas dalam 24 jam" di sini = pemalsuan angka (T-13.7)
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
    return True


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
    bulan itu — arahnya menahan order, tidak pernah memerintahkannya. Jalur baris
    kosong ini keputusan SADAR (diizinkan teks putusan, dipin tes
    `test_batal_on_fresh_row_is_deliberate_and_silent`). Inilah salah satu dari
    lima jalan menulis `dca_status` (lihat STATUS_DCA); tanpa fungsi ini Task 14
    tergoda menulis kolomnya langsung, yang dilarang.

    Mengembalikan True bila batal diterapkan, False bila ditolak prasyarat
    (review Minor 4) — pemanggil (Task 14) bisa memberi tahu pengguna bahwa
    "bulan ini sudah selesai, Batal tidak berlaku".
    """
    baris = _baris(conn)
    bulan = _bulan(ts)
    if (baris["last_dca_date"] == bulan
            and baris["dca_status"] in ("confirmed", "missed")):
        return False  # sudah selesai bulan ini — tombol basi tidak menghapus keputusan (T-13.5)
    if baris["last_dca_date"] not in (None, bulan):
        return False  # baris memegang bulan lain — tombol lintas-bulan tidak berlaku (T-13.5)
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'batal' WHERE id = 1",
        (bulan,),
    )
    conn.commit()
    return True


def mark_skip(conn, ts):
    """Tutup bulan ini sebagai "dilewati": percobaan gagal diusulkan, tanpa jendela.

    Inilah penanda "bulan dilewati" untuk percobaan yang gagal diusulkan
    (spread terlalu lebar, saldo tidak cukup, gerbang lain menolak usulan
    sebelum pesan konfirmasi terkirim): bulan ditutup tanpa jendela sama
    sekali, tanpa pengingat, dan tanpa catatan dca_missed — persis perilaku
    `dca_state` terhadap "batal". Keputusan pengguna 2026-10-17: percobaan
    sekali per bulan, gagal diusulkan = bulan dilewati, tanpa percobaan
    ulang. Bulan berikutnya berjalan normal (mark_pending tanpa syarat).

    Mengapa bukan `mark_batal` biasa: ia menolak baris yang memegang bulan
    lain (T-13.5), sedangkan di sini baris MEMANG memegang bulan lalu (bulan
    berjalan belum tersentuh) — itulah alasan fungsi ini ada.

    Menulis HANYA `last_dca_date` + `dca_status = 'batal'`, lalu commit.
    `dca_sent_ts` TIDAK disentuh: tidak ada pesan yang pernah terkirim, jadi
    mengisi kolom waktu kirim dengan apa pun = mengarang angka (sejajar
    mark_missed prasyarat 3 dan mark_confirmed T-13.3).

    NO-OP + False bila `last_dca_date` sudah bulan `ts` — jangan menimpa
    jendela yang sedang awaiting atau keputusan yang sudah tercatat. True
    bila bulan ditutup.
    """
    baris = _baris(conn)
    if baris["last_dca_date"] == _bulan(ts):
        return False  # bulan sudah ada — jendela/keputusan tidak ditimpa
    conn.execute(
        "UPDATE bot_state SET last_dca_date = ?, dca_status = 'batal' WHERE id = 1",
        (_bulan(ts),),
    )
    conn.commit()
    return True


def boleh_usul_dca(conn, now_ts):
    """True bila DCA bulanan masih boleh diusulkan pada `now_ts` — predikat murni.

    Dipakai `main_loop.tick_cycle` (Task 15.4) sebelum menyusun usulan DCA.
    Fungsi ini tidak menulis apa pun dan tidak memeriksa DND — tick_cycle
    hanya berjalan jam aktif, jadi DND urusan pemanggil.

    Tiga syarat, semuanya wajib:

    1. Bulan ini BELUM TERSENTUH (`baris[0] != _bulan(now_ts)`): jendela yang
       terkirim, terkonfirmasi, terlewat, maupun dibatalkan semuanya menutup
       peluang — percobaan sekali per bulan. Termasuk keputusan pengguna
       2026-10-17: Setuju ditekan, order gagal keluar sekalipun, bulan ini
       SELESAI, tidak pernah ditawarkan ulang.
    2. Tanggal 1 menunggu jam kirim: `tm_mday == 1 and tm_hour <
       DCA_SEND_HOUR` → False (kirim pukul 09.00 lokal — kabel Task 13 #6).
       Tanggal 2 dst. telat boleh: bot yang mati di tanggal 1 mengusulkan
       begitu hidup lagi, selama syarat 3 terpenuhi.
    3. Jendela 24 jam dari sekarang harus berakhir di bulan yang SAMA
       (`_bulan(now_ts + DCA_WINDOW_H * 3600) == _bulan(now_ts)`) — kabel
       T-13: `dca_state` mengembalikan ("none", 0.0) pada ketidakcocokan
       bulan SEBELUM aritmetika jendela, jadi jendela yang melintasi batas
       bulan tidak bisa tercatat dan "Setuju" pengguna akan sia-sia diam-diam.
       Lewat ambang ini bulan dilewati (mark_skip), bulan depan jalan normal.
    """
    baris = _baris(conn)
    if baris["last_dca_date"] == _bulan(now_ts):
        return False  # bulan ini sudah terkirim/batal/terkonfirmasi/terlewat
    sekarang = _lokal(now_ts)
    if sekarang.day == 1 and sekarang.hour < DCA_SEND_HOUR:
        return False  # tanggal 1, sebelum jam kirim 09.00
    return _bulan(now_ts + DCA_WINDOW_H * 3600) == _bulan(now_ts)


def dca_state(conn, now_ts):
    """Status DCA bulan berjalan: ("none" | "awaiting" | "confirmed" | "missed", sisa_detik).

    Jendela 24 jam dihitung dari waktu pengiriman; batas akhir eksklusif — tepat
    24 jam sesudah pengiriman sudah "missed". Lewat batas, shadow_log dicatat
    tepat sekali pada siklus transisi (F8), bukan setiap siklus.
    `sisa_detik` hanya bermakna untuk "awaiting"; status lain selalu 0.0.
    Status "batal" (mark_batal) menghasilkan ("none", 0.0) — tanpa jendela,
    tanpa pengingat, tanpa catatan missed.

    Catatan kabel Task 14: pada KETIDAKCOCOKAN BULAN fungsi ini mengembalikan
    ("none", 0.0) SEBELUM aritmetika jendela dijalankan — jendela yang dikirim
    hari terakhir bulan dan lewat hari pertama bulan berikutnya karena itu
    TIDAK PERNAH tercatat otomatis oleh transisi ini, dan rekaman manualnya
    lintas bulan tidak idempoten (lihat mark_missed prasyarat 3 & paragraf
    sisa kecelakaan). Rencana tidak mematok hari kirim, jadi Task 14 sebaiknya
    mengirim pada tanggal 1 pukul 09.00 atau menghindari jendela yang melintasi
    batas bulan (review Minor 3 putaran ini).

    Gagal keras, bukan diam-diam: `dca_status` di luar STATUS_DCA (penulisan
    langsung yang terlarang) → ValueError; `dca_sent_ts` NULL dengan bulan terisi
    → ValueError — data rusak tidak boleh ditebak (review T-13.4 & Minor 2).
    """
    baris = _baris(conn)
    if baris["last_dca_date"] != _bulan(now_ts):
        return ("none", 0.0)
    status = baris["dca_status"]
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
    kirim = baris["dca_sent_ts"]
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
    jam = _lokal(now_ts).hour
    if jam not in REMINDER_HOURS:
        return False
    if in_dnd(now_ts):
        return False
    return dca_state(conn, now_ts)[0] == "awaiting"
