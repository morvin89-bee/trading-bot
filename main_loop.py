"""Loop utama: denyut jantung, sakelar mode bayangan, dan integrasi modul.

Modul ini tidak pernah membuka jaringan dari dalam loop: semua jalur
keluar — konteks pasar, gerbang spread, Telegram, dan jalur ke bursa —
disuntikkan pemanggil; jaringan nyata hanya lewat blok `__main__` yang
menyuntik pembaca/fetch produksi. Modul juga tidak pernah membaca `.env`
untuk kunci dagang dan tidak memiliki kemampuan menempatkan order.
`main()` sendiri hanya bisa membangun objek bursa lewat `transport_factory`
yang diberikan, dan tanpa factories itu ia tidak membangun apa pun.

Urutan `tick_cycle` (berhenti di titik pertama yang gagal):

1. Rekonsiliasi `portfolio.db` vs bursa — selisih apa pun = berhenti, SEBELUM
   ada usulan apa pun (spec §6.3/§6.6).
2. Izin bursa dibaca ulang: `canWithdraw` aktif = berhenti apa pun.
3. **Polling Telegram.** Proses ini adalah SATU-SATUNYA konsumen `getUpdates`
   di seluruh program; tanpa langkah ini gerbang bersifat tulis-saja dan
   setiap konfirmasi akan kedaluwarsa tanpa pernah terjawab (spec §7.1 butir 4,
   putusan F6).
4. Pengingat pasif DCA — maksimal satu pesan per slot jam (kabel T-13.1).
5. `engine.tick` — Proposals, dan tidak ada lain.
6. Gerbang mode bayangan: satu penolakan = reset ke nol; hari bayangan naik
   **maksimal sekali per hari lokal**.

Sakelar naik kelas hanya setelah `GO_LIVE_SHADOW_DAYS` hari bayangan BERSIH.
Angka itu tidak pernah ditulis inline di perbandingan: spec §9 menyebut rentang
7-14 hari, dan 7 adalah lantainya.

Keterbatasan yang disengaja dan perlu diketahui pembaca berikutnya:

- `telegram_gate` hanya bisa mengirim pesan; tidak ada fungsi untuk mengedit
  tombol pesan lama. Karena itu "mematikan keyboard" di sini berarti mengirim
  satu baris yang menyatakan tombolnya tidak berlaku lagi. Pengaman yang
  sesungguhnya ada di `engine.on_answer` langkah 0 (jawaban kedua berhenti
  sebelum menyentuh bursa) — sudah dipatok tesnya sendiri di sana.
- `main()` membangun konteks pasar HANYA bila pemanggil menyuntikkan
  `pembaca_konteks` (jalur CLI `__main__` menyuntik `baca_konteks`).
  Tanpanya loop memakai `_konteks_kosong()` dan tidak mengusulkan apa pun —
  diam dan benar, bukan menebak.
"""
import argparse
import dataclasses
import logging
import os
import re
import sys
import time
import types

import dca_window
import engine
import indodax_api
import indodax_transport
import pair_meta
import portfolio
import portfolio_store
import proposal
import reconcile
import rules
import safeguards
import scheduler
import shadow_log
import spread
import telegram_gate

# ABSOLUT: log kegagalan harus selalu jatuh ke folder modul, apa pun cwd
# proses. Cacat nyata 2026-10-20 21:40: auto-revive VBS mewarisi cwd = home,
# sehingga denyut bot tercatat ke C:\Users\KakiTangan\errors.log sementara
# hitungan jujur G5 membaca trading-bot\errors.log (dua file, satu kebenaran).
ERRORS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "errors.log")


def _samarkan_pesan_galat(pesan):
    """Samarkan token Telegram di pesan galat jaringan sebelum keluar.

    `requests` menyisipkan URL lengkap (termasuk `bot<id>:<rahasia>`) ke dalam
    pesan pemutusan koneksi. Cetak kasarnya = mencetak token ke `v1_loop.log`.
    """
    return re.sub(r"bot\d[\w:-]{10,}", "bot<disamarkan>", str(pesan))


def _catat_denyut_dilewatkan(pesan):
    """Catat denyut yang dilewati (tanpa keputusan) ke `errors.log` dengan cap waktu.

    Baris cetak `v1_loop.log` tidak punya cap waktu, jadi tidak bisa dihitung
    per hari untuk kejujuran hitungan periode shadow G5.
    """
    logging.warning("%s", _samarkan_pesan_galat(pesan))


def _catat_galat_telegram(e, now):
    """Satu baris jelas per JENIS galat Telegram per jam; tanpa token/chat id.

    Cacat 2026-11-16: token mati mencetak `401 Client Error ...` di setiap
    denyut (51 baris/5 jam) tanpa menyebut sebabnya. Klasifikasi
    (`telegram_gate.klasifikasi_galat`) mengubahnya menjadi satu baris yang
    bisa ditindaklanjuti, dibatasi sekali per jenis per jam.

    Galat autentikasi (401/403) juga memasang backoff `TELEGRAM_BACKOFF_401_S`:
    token salah tidak akan sembuh karena dicoba lagi 12 kali sejam.
    """
    jenis, keterangan = telegram_gate.klasifikasi_galat(e)
    if jenis in ("401", "403"):
        _BACKOFF_TELEGRAM["sampai"] = now + TELEGRAM_BACKOFF_401_S
    jam = int(now // 3600)
    if _TERAKHIR_LOG_TELEGRAM.get(jenis) != jam:
        _TERAKHIR_LOG_TELEGRAM[jenis] = jam
        sebab = keterangan or _samarkan_pesan_galat(e)
        print(f"Polling Telegram gagal — {sebab}")
    # Penanda: penjaga denyut tidak perlu mencetak galat yang sama lagi.
    setattr(e, "_telegram_dicatat", True)


def _catat_denyut(conn, *, now, galat=None):
    """Catat denyut penuh terakhir yang SUKSES (atau galatnya) di `bot_state`.

    Tanpa dua kolom ini, bot bisa gagal berjam-jam dan tidak ada satu angka
    pun yang menjawab "kapan terakhir bot benar-benar bekerja?" — dashboard
    tampak normal, preflight tetap GO (kejadian 2026-11-16).

    Denyut yang berjalan saat Telegram masih dalam backoff TIDAK dihitung
    sukses: operator tidak bisa menyetujui apa pun, jadi mencatat "sehat"
    hanya akan menyembunyikan cacat yang sedang berlangsung.
    """
    dca_window.ensure_bot_state(conn)
    if galat is None:
        if now < _BACKOFF_TELEGRAM["sampai"]:
            return
        conn.execute(
            "UPDATE bot_state SET denyut_ok_ts = ?, galat_terakhir = NULL "
            "WHERE id = 1", (now,))
    else:
        conn.execute(
            "UPDATE bot_state SET galat_terakhir = ? WHERE id = 1",
            (_samarkan_pesan_galat(galat)[:200],))
    conn.commit()


class _CetakBerjam:
    """Bungkus stdout: setiap baris keluar dengan cap waktu WIB.

    `cmd >>` di `run_v1_hidden.vbs`/`run_v1.bat` menulis stdout mentah tanpa
    cap waktu, sehingga baris `v1_loop.log` tidak bisa dihitung per hari.
    Cap ditambahkan di satu tempat ini, sebelum tulisan keluar. Mesin wajib
    +07:00 (`main()` menolak start bila bukan), jadi `localtime` = WIB.
    """

    def __init__(self, aliran, jam):
        self._aliran = aliran
        self._jam = jam
        self._awal_baris = True

    def write(self, teks):
        for bagian in teks.splitlines(keepends=True):
            if self._awal_baris and bagian.strip():
                cap = time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(self._jam()))
                self._aliran.write(f"{cap} WIB ")
            self._aliran.write(bagian)
            self._awal_baris = bagian.endswith("\n")
        return len(teks)

    def flush(self):
        self._aliran.flush()

    def __getattr__(self, nama):
        """Teruskan atribut lain (`encoding`, `isatty`, ...) ke aliran asli."""
        return getattr(self._aliran, nama)


# Denyut jantung: lima menit (plan:1116).
HEARTBEAT_S = 300

# Umur maksimum "denyut terakhir sukses" sebelum dashboard/preflight
# menganggap bot mati atau macet (2026-11-16). Tiga kali denyut jantung:
# satu denyut terlewat bukan berita, tiga kali berturut-turut berarti bot
# tidak bekerja dan tidak ada yang tahu.
DENYUT_BASI_S = 900

# Batas percobaan kirim konfirmasi DCA per pair per bulan, TERMASUK yang
# pertama. Gangguan Telegram sementara diulang, tetapi tidak tanpa batas:
# setelah habis bulan ditutup dan pengguna diberi tahu.
DCA_MAX_PERCOBAAN_KIRIM = 3

# Jarak minimal antar percobaan kirim DCA (30 menit). Tanpa ini retry
# mengikuti denyut 5 menit dan menghabiskan tiga percobaan dalam 10 menit
# untuk gangguan yang mungkin pulih sejam kemudian.
DCA_RETRY_MIN_INTERVAL_S = 1800

# Tidak ada usulan/percobaan DCA baru setelah jam ini (waktu lokal). Jendela
# yang sudah terbuka tetap bisa dijawab sampai batas 24 jamnya.
DCA_JAM_TERAKHIR = 21

# Jarak bangun antar pembacaan tombol Telegram. Jendela konfirmasi reaktif
# 90 detik (`proposal.REACTIVE_WINDOW_S`) harus BISA dipenuhi pengguna:
# kalau `getUpdates` hanya dibaca per denyut (300 dtk), setiap tekanan tombol
# terbaca ±5 menit sesudahnya dan selalu berstatus hangus — tombol Setuju
# jadi tidak berguna sama sekali. 15 detik memberi pengguna sisa ±60 detik
# setelah pesan diterima (rancangan periode shadow, keputusan "dua irama").
POLL_GAP_S = 15

# Jeda minimum antar sapa awal ("Bot semi-otomatis berjalan") untuk proses
# yang berbeda. Crash-loop (proses mati-hidup tiap beberapa detik) tidak boleh
# mengirim pesan sapa tiap restart; penandanya disimpan di `bot_state`.
SAPA_MIN_INTERVAL_S = 3600

# Ulangan kirim sapa awal (2026-11-16): satu percobaan per proses terlalu
# rapuh — `v1_loop.log` 2026-11-07..13 mencatat lima "Pesan sapa awal tidak
# terkirim" beruntun. Batasnya jumlah DAN waktu (3 x 5 dtk = 10 dtk), lalu
# polling jalan terus tanpa menunggu Telegram pulih.
SAPA_ULANG_MAKS = 3
SAPA_JEDA_ULANG_S = 5

# Backoff galat autentikasi Telegram (2026-11-16): token mati membuat bot
# memukul `getUpdates` tiap denyut (51 kali dalam 5 jam di `errors.log`)
# tanpa satu pun hasil. 401/403 berarti token/chat salah — mengulang tiap
# lima menit tidak akan menolong, jadi percobaan dibatasi satu per 10 menit.
TELEGRAM_BACKOFF_401_S = 600

# Cetak ulang log kegagalan: tiap ke-N percobaan GAGAL BERTURUT-TURUT
# (I1 review 2026-10-18). Baris pertama tetap muncul di percobaan pertama.
# Tanpa syarat ini kegagalan permanen menyisakan SATU baris lalu sunyi
# selamanya; mencetak tiap bangun justru banjir (5.760 baris/hari pada
# irama 15 detik). Pemulihan dicetak terpisah saat penanda kembali turun
# (Review Focus #2: "pemulihan dicetak sekali").
ULANG_LOG_KEGAGALAN = 20

# Jam aktif DITURUNKAN dari `dca_window.DND` (23, 7) — bukan daftar jam kedua.
# Menyalinnya berarti dua sumber kebenaran yang bisa melenceng satu jam.
ACTIVE_HOURS = (dca_window.DND[1], dca_window.DND[0])   # (7, 23)

# Lantai syarat sakelar (spec §9: "7-14 hari mode bayangan"). Nama, bukan literal.
GO_LIVE_SHADOW_DAYS = 7

# Offset zona mesin yang benar untuk seluruh jadwal bot: +07:00 (WIB).
# `dca_window` memakai Asia/Jakarta eksplisit; modul lain masih localtime,
# jadi mesin dengan offset lain DITOLAK start (ragu = jangan jalan).
ZONA_OFFSET_DETIK = 7 * 3600


def _offset_zona_lokal(now_ts):
    """Offset zona lokal mesin (detik) pada `now_ts`; `None` bila tak terbaca."""
    try:
        return int(time.localtime(now_ts).tm_gmtoff)
    except (AttributeError, OverflowError, OSError, ValueError):
        return None


def _teks_offset(detik):
    """Format offset detik sebagai `+07:00` / `-03:30` untuk pesan."""
    if detik is None:
        return "tidak terbaca"
    tanda = "+" if detik >= 0 else "-"
    detik = abs(int(detik))
    return f"{tanda}{detik // 3600:02d}:{(detik % 3600) // 60:02d}"

# Keadaan proses (bukan persistensi):
#   usulan yang menunggu jawaban pengguna — dipolling, bukan diambil dari DB,
#   karena `engine.on_answer` butuh objek Proposal lengkap.
_USULAN_MENUNGGU = {}
# Slot pengingat yang sudah dipakai: (tanggal lokal, jam).
_SLOT_PENGINGAT = set()
# Offset getUpdates terakhir — pemanggilan berikutnya hanya membaca yang baru.
_OFFSET_TELEGRAM = {"offset": 0}
# Bulan "YYYY-MM" yang sudah diberi tahu bahwa DCA-nya berakhir tanpa order.
_SUDAH_DIBERITAHU_DCA_GAGAL = set()
# (bulan, pair) yang sudah diberi tahu bahwa percobaan kirimnya habis
# sementara jendela DCA masih terbuka.
_SUDAH_DIBERITAHU_RETRY_HABIS = set()
# Tanggal lokal yang sudah diberi tahu bahwa metadata pair basi/hilang.
_SUDAH_DIBERITAHU_METADATA = set()
# Tanggal lokal yang sudah diberi tahu bahwa harga aset di luar target hilang.
_SUDAH_DIBERITAHU_HARGA = set()
# Satu baris per JENIS galat Telegram per jam: token mati tidak boleh
# menghasilkan 12 baris/jam yang sama, dan tidak boleh sunyi sama sekali.
_TERAKHIR_LOG_TELEGRAM = {}
_BACKOFF_TELEGRAM = {"sampai": 0.0}


def reset_state():
    """Kosongkan seluruh keadaan proses (dipakai tes, dan oleh `main`)."""
    _USULAN_MENUNGGU.clear()
    _SLOT_PENGINGAT.clear()
    _OFFSET_TELEGRAM["offset"] = 0
    _SUDAH_DIBERITAHU_DCA_GAGAL.clear()
    _SUDAH_DIBERITAHU_RETRY_HABIS.clear()
    _SUDAH_DIBERITAHU_METADATA.clear()
    _SUDAH_DIBERITAHU_HARGA.clear()
    _TERAKHIR_LOG_TELEGRAM.clear()
    _BACKOFF_TELEGRAM["sampai"] = 0.0
    engine.reset_state()


def load_state(conn):
    """Baca state bot dari tabel `bot_state` milik `dca_window`.

    Mengembalikan dict berisi `v1_enabled` (bool), `shadow_day` (int), dan
    `last_dca_date` (str "YYYY-MM" atau None).

    WAJIB membaca lewat `dca_window.ensure_bot_state` — membuat tabel atau
    pembuat state kedua dilarang keras (putusan R-G): dua bentuk skema yang
    berbeda akan membuat satu modul menulis dan modul lain membaca kolom yang
    tidak sama.
    """
    dca_window.ensure_bot_state(conn)
    baris = conn.execute(
        "SELECT last_dca_date, v1_enabled, shadow_day FROM bot_state WHERE id = 1"
    ).fetchone()
    return {
        "last_dca_date": baris[0],
        "v1_enabled": bool(baris[1]),
        "shadow_day": int(baris[2]),
    }


def set_shadow_day(conn, day, *, now=None):
    """TETAPKAN `shadow_day` ke `day` — sifatnya menetapkan, bukan menambah.

    Panggilan kedua dengan angka lain MENGGANTI, bukan menambah: fungsi ini
    menetapkan keadaan, bukan menghitungnya.

    LIVE MANUAL (putusan 2026-10-21): fungsi ini TIDAK PERNAH menyalakan
    `v1_enabled` — walau `day >= GO_LIVE_SHADOW_DAYS`. Mencapai lantai 7 hanya
    membuka JENDELA keputusan; tombol hidup ditekan manusia lewat
    `aktifkan_live`. Satu-satunya pengaruh pada sakelar: MEMATIKAN, bila
    `day < 7` (reset penolakan harus tetap mematikan eksekusi — keselamatan
    ini tidak boleh hilang).

    Setiap perubahan kolom sakelar dicatat ke `bot_state_audit` dalam
    transaksi yang sama. `now` boleh disuntikkan; bawaan `time.time()` hanya
    untuk pemanggil lama (tes wajib menyuntik).
    """
    dca_window.ensure_bot_state(conn)
    if now is None:
        now = time.time()
    lama = tuple(conn.execute(
        "SELECT v1_enabled, shadow_day FROM bot_state WHERE id = 1").fetchone())
    if int(day) < GO_LIVE_SHADOW_DAYS:
        conn.execute(
            "UPDATE bot_state SET shadow_day = ?, v1_enabled = 0 WHERE id = 1",
            (int(day),),
        )
    else:
        conn.execute(
            "UPDATE bot_state SET shadow_day = ? WHERE id = 1", (int(day),)
        )
    baru = tuple(conn.execute(
        "SELECT v1_enabled, shadow_day FROM bot_state WHERE id = 1").fetchone())
    dca_window.catat_audit_sakelar(
        conn, ts=now, sumber="set_shadow_day", lama=lama, baru=baru)
    conn.commit()


def aktifkan_live(conn, *, now=None):
    """NYALAKAN sakelar eksekusi — SATU-SATUNYA jalan ke `v1_enabled = 1`.

    Syarat keras: `shadow_day >= GO_LIVE_SHADOW_DAYS`. Di bawah lantai 7,
    fungsi ini MENOLAK (False) dan tidak menyentuh sakelar sama sekali —
    periode bayangan tidak bisa dipotong. Bila lolos, nyalakan dan True.

    Sengaja terpisah dari `set_shadow_day`: menghitung hari adalah mesin
    (otomatis), menyalakan eksekusi adalah manusia (sadar). Dua hal ini tidak
    boleh lagi menjadi satu baris — di situlah keputusan go-live hidup.

    Penolakan tidak dicatat ke `bot_state_audit`: tidak ada perubahan.
    """
    dca_window.ensure_bot_state(conn)
    if now is None:
        now = time.time()
    baris = conn.execute(
        "SELECT shadow_day, v1_enabled FROM bot_state WHERE id = 1"
    ).fetchone()
    if int(baris[0]) < GO_LIVE_SHADOW_DAYS:
        return False
    lama = (baris[1], baris[0])
    conn.execute("UPDATE bot_state SET v1_enabled = 1 WHERE id = 1")
    dca_window.catat_audit_sakelar(
        conn, ts=now, sumber="aktifkan_live",
        lama=lama, baru=(1, int(baris[0])))
    # Memori TP dipisah per mode: penjualan simulasi tidak boleh menghalangi
    # penjualan nyata. Baris live (bila ada) dipertahankan.
    shadow_log.hapus_take_profit_mode(conn, "shadow")
    conn.commit()
    return True


def _hari_lokal(now_ts):
    """Tanggal lokal "YYYY-MM-DD" — satu-satunya bentuk yang dibaca log bayangan."""
    return time.strftime("%Y-%m-%d", time.localtime(now_ts))


def _commit_singkat(akar=None):
    """Hash commit pendek dari berkas `.git` — tanpa subprocess, tanpa jaringan.

    Status harian menyebut versi kode yang benar-benar berjalan; membaca
    `.git/HEAD` (dan `packed-refs` bila ref-nya sudah dipadatkan) cukup untuk
    itu. Gagal membaca = "tidak diketahui", bukan tebakan.
    """
    akar = os.path.dirname(os.path.abspath(__file__)) if akar is None else akar
    try:
        with open(os.path.join(akar, ".git", "HEAD"), encoding="ascii") as f:
            isi = f.read().strip()
        if not isi.startswith("ref: "):
            return isi[:7]                 # HEAD terlepas (detached)
        ref = isi[len("ref: "):]
        try:
            with open(os.path.join(akar, ".git", *ref.split("/")),
                      encoding="ascii") as f:
                return f.read().strip()[:7]
        except OSError:
            with open(os.path.join(akar, ".git", "packed-refs"),
                      encoding="ascii") as f:
                for baris in f:
                    if baris.rstrip().endswith(" " + ref):
                        return baris.split()[0][:7]
        return "tidak diketahui"
    except OSError:
        return "tidak diketahui"


# Hash commit proses ini: dibaca SEKALI saat modul dimuat (start), lalu
# dipakai semua pesan status. Nilainya tidak berubah selama proses hidup;
# membaca ulang tiap pesan hanya menambah I/O tanpa menambah kebenaran.
_COMMIT = _commit_singkat()


def status_ringkas(conn, *, now, shadow, transport=None, commit=None):
    """Teks status untuk Telegram: mode, hari bayangan, metadata, DCA, commit.

    Tanpa rahasia: hanya mode/angka/status dan hash commit — tidak ada token,
    chat id, atau kunci API. `transport=None` berarti izin kunci tidak bisa
    dibaca; itu ditulis apa adanya, bukan ditebak.

    Peringatan ditambahkan HANYA bila sakelar nyala dan kunci tidak cocok
    (`safeguards.permissions_ok`), karena itulah keadaan yang membuat
    eksekusi mustahil jalan sementara bot terlihat "hidup".
    """
    keadaan = load_state(conn)
    baris = conn.execute(
        "SELECT dca_status FROM bot_state WHERE id = 1").fetchone()
    if keadaan["last_dca_date"] != dca_window._bulan(now):
        dca = "belum ada jendela bulan ini"
    else:
        dca = {
            "awaiting": "menunggu jawaban Anda",
            "confirmed": "dikonfirmasi",
            "missed": "terlewat (missed)",
            "batal": "dibatalkan",
        }.get(baris[0], f"status tak dikenal: {baris[0]!r}")

    umur = pair_meta.umur_tertua(conn, now=now)
    if umur is None:
        meta = "belum ada"
    elif umur > pair_meta.MAX_AGE_S:
        meta = f"BASI ({umur / 3600:.1f} jam)"
    else:
        meta = f"segar ({umur / 60:.0f} menit)"

    teks = [
        f"Status bot — mode {'BAYANGAN' if shadow else 'EKSEKUSI'}",
        f"- hari bayangan: {keadaan['shadow_day']}/{GO_LIVE_SHADOW_DAYS}",
        f"- metadata pair: {meta}",
        f"- DCA bulan ini: {dca}",
        f"- commit: {commit or _COMMIT}",
    ]
    if not shadow:
        if transport is None:
            teks.append(
                "PERINGATAN: sakelar EKSEKUSI NYALA tetapi tidak ada "
                "transport — eksekusi tidak bisa jalan.")
        else:
            alasan = safeguards.permissions_ok(
                transport.can_trade, transport.can_withdraw, switch_on=True)
            if alasan is not None:
                teks.append(f"PERINGATAN: {alasan}")
    return "\n".join(teks)


def _tabel_ada(conn, nama):
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (nama,),
    ).fetchone()[0] > 0


def apply_shadow_gate(conn, *, now=None):
    """Terapkan aturan mode bayangan untuk satu hari lokal (spec §9).

    Dua halves, URUTANNYA mengikat:

    1. Bila ada penolakan pada HARI INI (`shadow_log.rejected_count`), hari
       bayangan di-nol-kan dan sakelar dimatikan. Pemeriksaan ini berjalan
       pada SETIAP pemanggilan — termasuk setelah hari ini sudah dihitung.
       Kalau tidak, penolakan sore hari bisa lolos hanya karena penanda hari
       sudah terisi pagi tadi.
    2. HANYA kalau penanda hari (`bot_state.shadow_checked_day`) belum sama
       dengan hari ini, `shadow_day` naik satu dan penanda diisi.

    Tanpa langkah 2, denyut jantung lima menit sekali akan menaikkan
    `shadow_day` dua belas kali sehari dan menyalakan sakelar sekitar pukul
    15.00 pada hari pertama — melewati satu-satunya syarat keamanan yang
    menjadi alasan proyek ini ada.

    `now` boleh disuntikkan (waktu lokal, detik sejak epoch). Tanpa itu
    dipakai `time.time()`.
    """
    if now is None:
        now = time.time()
    hari_ini = _hari_lokal(now)

    # Tabel proposals milik shadow_log; log bisa belum pernah dibuat, dan
    # "belum ada catatan" sama dengan "nol penolakan" — bukan galat.
    ditolak = 0
    if _tabel_ada(conn, "proposals"):
        ditolak = shadow_log.rejected_count(conn, hari_ini)

    if ditolak > 0:
        set_shadow_day(conn, 0, now=now)
        return

    dca_window.ensure_bot_state(conn)
    baris = conn.execute(
        "SELECT shadow_day, shadow_checked_day FROM bot_state WHERE id = 1"
    ).fetchone()
    if baris[1] == hari_ini:
        return   # hari ini sudah dihitung — denyut berikutnya tidak menambah
    # Mesin MENGHITUNG hari saja. Menyalakan `v1_enabled` di luar fungsi ini —
    # itu pekerjaan `aktifkan_live`, yang dipanggil manusia (live manual).
    set_shadow_day(conn, int(baris[0]) + 1, now=now)
    conn.execute(
        "UPDATE bot_state SET shadow_checked_day = ? WHERE id = 1", (hari_ini,)
    )
    conn.commit()


def should_run_habitual(now_ts):
    """True di luar jendela DND — mendelegasikan ke `dca_window.in_dnd`.

    DND 23.00-07.00 lokal: batas awal inklusif, batas akhir eksklusif
    (23.00 masuk, 07.00 keluar). Aturan itu milik `dca_window`; menyalinnya
    di sini berarti dua tempat yang harus selalu sepakat.
    """
    return not dca_window.in_dnd(now_ts)


def _konteks_kosong():
    """Konteks tanpa posisi — dipakai `main()` yang belum punya pembaca pasar."""
    return engine.Context(
        holdings=[], prices={}, free_idr=0.0, reserve_idr=0.0,
        today_counts={}, last_fill_ts={}, held_pairs_24h=set(),
    )


def baca_konteks(conn, *, now=None, sinkron=portfolio.sync_trades,
                 ambil_info=indodax_api.account_info,
                 ambil_harga=portfolio.fetch_price):
    """Baca konteks pasar SEBENARNYA untuk satu denyut (pembaca produksi).

    Urutan dan kejujuran angkanya, sesuai rancangan periode shadow:

    1. `sinkron(conn)` — tarik riwayat trade terbaru (read-only; kegagalan per
       pair sudah ditelan di dalam `portfolio.sync_trades`).
    2. `ambil_info()` — respons MENTAH `account_info`. Hanya bentuk TAPIv2
       (`balances` list) yang dipercaya; bentuk lain = `ValueError`, karena
       tanpa bentuk yang dikenal `free` tidak bisa dipastikan (ragu = tidak
       mengambil keputusan).
    3. `ambil_harga` untuk SEMUA pair di `portfolio.COIN_PAIRS`. Pair di
       `rules.DEFAULT_TARGET` WAJIB punya harga — satu pun `None` =
       `ValueError`, tidak ada angka parsial yang menyusup ke denyut (harga
       hilang = tidak ada keputusan, spec §6.3). Pair di luar target (mis.
       BCH) boleh `None`: tidak ada keputusan order yang memakai nilainya,
       jadi harga hilang = peringatan + aset disembunyikan (2026-11-15),
       bukan halt seluruh denyut.
    4. `free_idr` dihitung dari field `free` SAJA (bukan `free + locked`):
       saldo yang terkunci order tidak boleh dianggap tersedia (spec §6.4
       "free sudah bersih"). `reserve_idr` dari `portfolio.reserve_idr()`
       (`RESERVE_IDR` di `.env`); tidak diset = 0.0, dan reserve buy memang
       tidak pernah diusulkan tanpa angka itu.
    5. `today_counts` / `last_fill_ts` / `held_pairs_24h` diturunkan dari
       riwayat `trades` dengan WAKTU LOKAL (`time.localtime`) — bukan isi
       yang dikarang, bukan `utcnow`.

    `dca_enabled` dibiarkan default `False`: yang mengisinya per siklus
    adalah `main_loop` lewat `dca_window` (satu-satunya pemilik `conn` di
    lapisan ini).
    """
    now = time.time() if now is None else now

    sinkron(conn)

    info = ambil_info()
    if not (isinstance(info, dict) and isinstance(info.get("balances"), list)):
        raise ValueError("bentuk saldo tidak dikenal — bukan daftar balances TAPIv2")

    prices = {}
    for pair in portfolio.COIN_PAIRS.values():
        harga = ambil_harga(pair)
        if harga is None:
            # Hanya cache SEGAR (<= 120 dtk); selain itu harga hilang.
            harga = portfolio.cached_price(pair, now=now)
        prices[pair] = harga
    hilang = [pair for pair, harga in prices.items()
              if harga is None and pair in rules.DEFAULT_TARGET]
    if hilang:
        raise ValueError("harga gagal dibaca: " + ", ".join(sorted(hilang)))

    free_idr = 0.0
    for b in info["balances"]:
        asset = str(b.get("asset") or "").strip().lower()
        if asset in ("idr", "rp"):
            try:
                free_idr += float(b.get("free") or 0)
            except (TypeError, ValueError):
                raise ValueError("nilai free IDR tidak terbaca")

    coins = {k: v for k, v in portfolio.parse_balances(info).items()
             if k != "IDR"}
    free_coins = {k: v for k, v in portfolio.parse_free_balances(info).items()
                  if k != "IDR"}
    trades = portfolio_store.list_trades(conn)
    holdings = portfolio.compute_holdings(
        trades, coins, prices, free_balances=free_coins)

    hari_ini = time.strftime("%Y-%m-%d", time.localtime(now))
    today_counts: dict = {}
    last_fill_ts: dict = {}
    held_pairs_24h: set = set()
    for t in trades:
        side = (t.get("side") or "").lower()
        if side not in ("buy", "sell") or t.get("ts") is None:
            continue
        ts = float(t["ts"])
        pair = t["pair"]
        last_fill_ts[pair] = max(last_fill_ts.get(pair, ts), ts)
        if time.strftime("%Y-%m-%d", time.localtime(ts)) == hari_ini:
            today_counts[pair] = today_counts.get(pair, 0) + 1
        if now - ts <= 86400:
            held_pairs_24h.add(pair)

    return engine.Context(
        holdings=holdings, prices=prices, free_idr=free_idr,
        reserve_idr=portfolio.reserve_idr(), today_counts=today_counts,
        last_fill_ts=last_fill_ts, held_pairs_24h=held_pairs_24h,
    )


def _ingat(usulan):
    """Daftarkan usulan yang menunggu jawaban supaya poling bisa merujuknya."""
    for p in usulan:
        _USULAN_MENUNGGU[p.id] = p
    return usulan


def _muat_usulan(conn, *, now):
    """Pulihkan usulan yang masih menunggu jawaban dari `shadow_log`.

    Tanpa ini restart membuat tombol yang sudah dikirim ke Telegram menjadi
    inert ("id tak dikenal"), lalu jendela DCA 24 jam berakhir dengan catatan
    `dca_missed` palsu.

    Usulan DCA hanya dimuat bila jendelanya masih resmi `awaiting`. Baris
    `proposed` yang tertinggal setelah Batal/confirmed/missed tidak boleh
    hidup lagi hanya karena restart — order tanpa jendela yang sah.
    """
    status_dca = dca_window.dca_state(conn, now)[0]
    for p in shadow_log.pending(conn, now):
        if p.rule == rules.RULE_DCA and status_dca != "awaiting":
            continue
        _USULAN_MENUNGGU[p.id] = p


def _muat_offset(conn):
    """Pulihkan offset `getUpdates` terakhir dari `bot_state`."""
    dca_window.ensure_bot_state(conn)
    baris = conn.execute(
        "SELECT telegram_offset FROM bot_state WHERE id = 1"
    ).fetchone()
    _OFFSET_TELEGRAM["offset"] = int(baris[0]) if baris is not None else 0


def _simpan_offset(conn, offset):
    """Simpan offset `getUpdates` agar restart tidak memproses ulang update lama."""
    dca_window.ensure_bot_state(conn)
    conn.execute(
        "UPDATE bot_state SET telegram_offset = ? WHERE id = 1", (offset,)
    )
    conn.commit()


def _sapa_boleh(conn, now):
    """True bila sapa awal terakhir sudah lewat `SAPA_MIN_INTERVAL_S`.

    Penanda disimpan di `bot_state` (bukan memori): crash-loop yang memulai
    proses baru tiap beberapa detik tidak boleh mengirim sapa tiap restart.
    """
    dca_window.ensure_bot_state(conn)
    terakhir = conn.execute(
        "SELECT sapa_terakhir_ts FROM bot_state WHERE id = 1"
    ).fetchone()[0]
    return terakhir is None or now - terakhir >= SAPA_MIN_INTERVAL_S


def _catat_sapa(conn, now):
    """Catat waktu sapa awal dikirim (pembatas anti crash-loop)."""
    dca_window.ensure_bot_state(conn)
    conn.execute(
        "UPDATE bot_state SET sapa_terakhir_ts = ? WHERE id = 1", (now,)
    )
    conn.commit()


def _status_kirim_dca(conn, now):
    """Pair DCA yang sudah terkirim dan jumlah percobaan gagal bulan ini."""
    bulan = time.strftime("%Y-%m", time.localtime(now))
    return shadow_log.dca_bulan_ini(conn, rules.RULE_DCA, bulan)


def _boleh_kirim_ulang_dca(conn, now, terkirim, gagal):
    """True bila jendela masih awaiting dan ada pair gagal yang boleh diulang.

    Tiga syarat per pair: belum terkirim, jumlah percobaan di bawah batas,
    dan sudah lewat `DCA_RETRY_MIN_INTERVAL_S` sejak percobaan gagal
    terakhir. Setelah `DCA_JAM_TERAKHIR` tidak ada percobaan baru sama
    sekali — jendela yang sudah terbuka tetap bisa dijawab.
    """
    if dca_window.dca_state(conn, now)[0] != "awaiting":
        return False
    if time.localtime(now).tm_hour >= DCA_JAM_TERAKHIR:
        return False
    return any(
        pair not in terkirim and jumlah < DCA_MAX_PERCOBAAN_KIRIM
        and (terakhir is None
             or now - terakhir >= DCA_RETRY_MIN_INTERVAL_S)
        for pair, (jumlah, terakhir) in gagal.items()
    )


def _beritahu_retry_habis(conn, now, telegram, terkirim, gagal):
    """Satu pesan per pair per bulan saat percobaan kirimnya habis.

    Hanya untuk jendela yang SUDAH terbuka (sebagian pair terkirim). Bulan
    yang belum tersentuh ditutup lewat `mark_skip` dan diberitahu oleh
    `_beritahu_dca_berakhir`, bukan di sini.
    """
    if dca_window.dca_state(conn, now)[0] != "awaiting":
        return
    habis = sorted(
        pair for pair, (jumlah, _ts) in gagal.items()
        if pair not in terkirim and jumlah >= DCA_MAX_PERCOBAAN_KIRIM)
    if not habis:
        return
    bulan = time.strftime("%Y-%m", time.localtime(now))
    kunci = (bulan, tuple(habis))
    if kunci in _SUDAH_DIBERITAHU_RETRY_HABIS:
        return
    _SUDAH_DIBERITAHU_RETRY_HABIS.add(kunci)
    telegram.line(
        f"Konfirmasi DCA {', '.join(habis)} gagal terkirim "
        f"{DCA_MAX_PERCOBAAN_KIRIM} kali — tidak dicoba lagi bulan {bulan}. "
        "Pair yang sudah terkirim tetap bisa dijawab."
    )


def _beritahu_dca_berakhir(conn, now, telegram, gagal, *, baru_skip):
    """Satu pesan bila DCA bulan ini berakhir tanpa order yang lengkap.

    `baru_skip` = bulan baru saja ditutup karena batas retry habis. Jalur
    kedua: jendela 24 jam lewat tanpa jawaban (`dca_state` -> "missed").
    Satu pesan per bulan; Batal/confirmed tidak diberitahu di sini.
    """
    if baru_skip:
        if not gagal:
            return
    elif dca_window.dca_state(conn, now)[0] != "missed":
        return
    bulan = time.strftime("%Y-%m", time.localtime(now))
    if bulan in _SUDAH_DIBERITAHU_DCA_GAGAL:
        return
    _SUDAH_DIBERITAHU_DCA_GAGAL.add(bulan)
    rincian = (
        "konfirmasi gagal terkirim untuk: " + ", ".join(sorted(gagal))
        if gagal else "konfirmasi tidak dijawab dalam 24 jam"
    )
    telegram.line(
        f"DCA bulan {bulan} berakhir tanpa order — {rincian}. "
        "Tidak ada order yang dikirim untuk pair itu."
    )


def _catat_dca(conn, now, usulan, *, tunda=False):
    """Buka atau tutup bulan DCA setelah satu tick — HANYA bila disebut.

    Usulan DCA di daftar berarti pesan benar-benar terkirim (`engine.tick`
    hanya mengembalikan yang terkirim) -> jendela resmi dibuka. DCA aktif
    tapi nol usulan terkirim = percobaan sudah habis -> bulan ditutup
    (keputusan pengguna 2026-10-17: sekali coba, gagal = dilewati).

    `tunda=True` adalah pengecualian tersempit: percobaan belum tuntas
    karena konfirmasi tidak terkirim atau tick berhenti sebelum sempat
    mengirim. Tidak ada jendela yang dibuka dan tidak ada uang bergerak,
    jadi bulan TIDAK ditutup; percobaan diulang pada tick berikutnya.

    Mengembalikan "pending" | "tunda" | "skip" — `skip` berarti bulan
    benar-benar ditutup pada panggilan ini, dasar pemberitahuan pengguna.
    """
    if any(p.rule == rules.RULE_DCA for p in usulan):
        # Jendela yang sudah terbuka TIDAK diperpanjang oleh retry parsial:
        # waktu kirim pertama adalah janji 24 jam yang sebenarnya.
        if dca_window.dca_state(conn, now)[0] != "awaiting":
            dca_window.mark_pending(conn, now)
        return "pending"
    if tunda:
        return "tunda"
    return "skip" if dca_window.mark_skip(conn, now) else "tunda"


def _terapkan_setuju_dca(conn, usulan, hasil, *, shadow, now, telegram):
    """Tutup jendela DCA hanya bila persetujuan benar-benar diterapkan.

    "sent" = order terkirim. Di mode bayangan tidak ada order, tetapi
    `on_answer` mencatat persetujuan sebagai outcome "setuju" — itulah
    persetujuan bayangan yang menutup jendela. Penolakan spread/gerbang,
    halt, atau konfirmasi hangus TIDAK menutup jendela: pengguna diberi tahu
    bahwa DCA bulan ini masih terbuka dan bisa dijawab lagi.
    """
    if hasil == "sent":
        dca_window.mark_confirmed(conn, now)
        return
    if shadow and shadow_log.outcome(conn, usulan.id) == "setuju":
        dca_window.mark_confirmed(conn, now)
        return
    if dca_window.dca_state(conn, now)[0] == "awaiting":
        telegram.line(
            "DCA bulan ini masih terbuka — jawaban belum diterapkan. "
            "Tekan Setuju lagi setelah gangguan berlalu, atau Batal bulan ini."
        )


def _jawab(conn, ctx, aksi, id_usulan, *, now, telegram, transport,
           shadow, verdict, revisi=0):
    """Teruskan satu jawaban pengguna ke `engine.on_answer`.

    `ctx.prices` WAJIB terisi dari ticker (kabel Task 12): `on_answer` membacanya
    untuk gerbang geser harga 0,5%. Kosong = setiap konfirmasi berbalik
    "ignored" tanpa alasan yang jujur.

    `revisi` adalah nomor revisi pesan yang tombolnya ditekan. Re-quote
    menaikkan revisi usulan; jawaban dari pesan lama (revisi lebih kecil)
    DITOLAK — pengguna tidak boleh menyetujui angka yang sudah tidak berlaku.

    F46: jendela DCA hanya disentuh bila jawaban rujuk ke usulan DCA yang
    benar-benar dikenal — id tak dikenal berarti jenis usulan tidak
    diketahui, jadi ragu dan tidak menulis (lihat tes F46).

    Jendela DCA ditutup SETELAH `on_answer` memberi hasil yang final —
    bukan sebelum, karena penolakan gerbang bukan persetujuan.
    """
    if aksi == "ulang":
        return apply_ulang(conn, ctx, now=now, telegram=telegram,
                           shadow=shadow, verdict=verdict)

    # Pencarian usulan lebih dulu: tombol hanya berarti bila id-nya dikenal
    # DAN usulannya ber-rule DCA (F46) — Setuju pada take-profit tidak boleh
    # menutup jendela DCA, dan id tak dikenal tidak boleh menyentuh apa pun.
    usulan = _USULAN_MENUNGGU.get(id_usulan)
    if usulan is not None and revisi != getattr(usulan, "revisi", 0):
        telegram.line(
            f"Usulan {id_usulan} berasal dari pesan lama (revisi {revisi}, "
            f"terbaru {getattr(usulan, 'revisi', 0)}) — tidak ada order "
            "dikirim. Tekan tombol pada pesan konfirmasi terbaru."
        )
        return "ignored"
    if aksi == telegram_gate.BUTTON_BATAL:
        if usulan is not None and usulan.rule == rules.RULE_DCA:
            if dca_window.mark_batal(conn, now):
                # Satu Batal menutup SELURUH usulan DCA bulan ini: kalau hanya
                # id yang ditekan yang dilepas, Setuju pada usulan DCA lain
                # tetap mengirim order padahal jendelanya sudah 'batal'.
                # Ingatan dikosongkan lebih dulu (tidak ada order yang bisa
                # lolos), lalu DB ditandai supaya restart tidak
                # menghidupkan kembali tombol yang sama.
                for pid in [pid for pid, p in _USULAN_MENUNGGU.items()
                            if p.rule == rules.RULE_DCA]:
                    _USULAN_MENUNGGU.pop(pid, None)
                shadow_log.void_pending(
                    conn, rules.RULE_DCA, now,
                    "Dibatalkan bersama jendela DCA bulan ini")
            else:
                telegram.line(
                    "Bulan ini sudah selesai, Batal bulan ini tidak berlaku."
                )

    if usulan is None:
        telegram.line(
            f"Usulan {id_usulan} sudah tidak dikenal — tidak ada order dikirim."
        )
        return "ignored"

    # Tanpa transport di luar mode bayangan, `on_answer` tidak punya siapa pun
    # untuk ditanya (F40). Berhenti di sini, bukan melempar AttributeError.
    if not shadow and transport is None:
        telegram.line(
            "Eksekusi belum dinyalakan — jawaban dicatat, tidak ada order dikirim."
        )
        if aksi == telegram_gate.BUTTON_SETUJU and usulan.rule == rules.RULE_DCA:
            _terapkan_setuju_dca(conn, usulan, None, shadow=shadow, now=now,
                                 telegram=telegram)
        return "ignored"

    hasil = engine.on_answer(
        conn, ctx, p=usulan, answer=aksi, now=now, transport=transport,
        telegram=telegram, shadow=shadow, verdict=verdict,
    )
    if aksi == telegram_gate.BUTTON_SETUJU and usulan.rule == rules.RULE_DCA:
        _terapkan_setuju_dca(conn, usulan, hasil, shadow=shadow, now=now,
                             telegram=telegram)

    # Pengaman sesungguhnya ada di langkah 0 `on_answer` (jawaban kedua
    # berhenti sebelum menyentuh bursa). Pesan ini hanya untuk hasil yang
    # sudah final; penolakan sebelum kirim masih bisa dicoba lagi (P1.4).
    # "ignored" karena harga bergeser/re-quote juga belum final selama
    # jendelanya masih terbuka dan usulan belum dijawab.
    outcome = shadow_log.outcome(conn, usulan.id)
    menunggu = outcome in (None, "proposed") and (
        hasil in ("gate_blocked", "halted")
        or (hasil == "ignored" and not proposal.is_expired(usulan, now))
    )
    if menunggu:
        telegram.line(
            f"Usulan {id_usulan} belum dieksekusi — tombol masih berlaku. "
            "Coba lagi setelah gangguan berlalu."
        )
    else:
        telegram.line(
            f"Usulan {id_usulan} sudah diproses. Tombol di pesan lama sudah tidak berlaku."
        )
    return hasil


def _ambil_update(telegram, fetch, *, now):
    """Ambil batch `getUpdates` TANPA memajukan offset Telegram.

    Pemanggil yang memutuskan kapan offset boleh maju: batch yang hanya
    DIPERIKSA (promosi `/ulang`, lihat `main`) tidak boleh mengonsumsi
    antrian — `tick_cycle` akan mengambil batch yang sama lagi dengan
    offset lama dan memprosesnya di denyut yang segar.

    Mengembalikan `None` bila sedang backoff galat autentikasi: API tidak
    disentuh sama sekali (2026-11-16). Galat yang benar-benar terjadi dicatat
    sekali per jenis per jam oleh `_catat_galat_telegram`, lalu dilempar
    kembali — "gagal" tidak boleh menyamar menjadi "tanpa update".
    """
    if now < _BACKOFF_TELEGRAM["sampai"]:
        return None
    try:
        return telegram_gate.get_updates(
            getattr(telegram, "token", ""),
            offset=_OFFSET_TELEGRAM["offset"],
            fetch=fetch,
        )
    except Exception as e:
        _catat_galat_telegram(e, now)
        raise


def _ada_perintah_ulang(updates):
    """True bila batch memuat perintah `/ulang` — sinyal promosi denyut."""
    for update in updates:
        parsed = telegram_gate.parse_update(update)
        if parsed is not None and parsed[0] == "ulang":
            return True
    return False


def _proses_batch(conn, ctx, updates, *, now, telegram, transport, shadow,
                  verdict, baca_harga=None, pembaca_konteks=None):
    """Proses SATU batch update; offset sudah maju oleh pemanggil.

    Mengembalikan True bila ada perintah `/ulang` di dalamnya — pemanggil
    (`tick_cycle`) lalu tidak menjalankan `engine.tick` lagi, karena dua
    usulan dari aksi dan waktu yang sama persis akan memakai
    `client_order_id` yang sama.

    `baca_harga(pair)` (opsional) dipanggil SEBELUM jawaban diproses untuk
    memperbarui `ctx.prices[pair]`: angka di pesan harus diuji terhadap harga
    SAAT INI, bukan harga denyut lima menit lalu (gerbang geser 0,5%, spec
    §6.3). Gagal membaca = `ctx.prices[pair] = None` → `price_still_valid`
    menolak → konfirmasi hangus. Tidak ada keputusan di atas harga basi.

    `pembaca_konteks(conn)` (opsional) dipanggil sebelum jawaban pertama
    diproses: saldo dan posisi bisa berubah sejak denyut terakhir (order
    lain terisi), dan `on_answer` menilai gerbang dengan angka yang
    diberikan. Gagal membaca = jawaban TIDAK diterapkan (ragu = jangan
    kirim); pengguna diberi tahu dan bisa menekan tombol lagi.
    """
    diulang = False
    for update in updates:
        try:
            parsed = telegram_gate.parse_update(update)
            if parsed is None:
                continue
            aksi, id_usulan, revisi = parsed
            # `/ulang` selalu lewat denyut penuh yang sudah membaca konteks
            # segar sebelum `tick_cycle`; membacanya lagi di sini hanya
            # menggandakan panggilan pembaca tanpa menambah kebenaran.
            if aksi != "ulang" and pembaca_konteks is not None:
                try:
                    ctx = pembaca_konteks(conn)
                except Exception as e:
                    telegram.line(
                        "Saldo/pasar gagal dibaca ulang sebelum menjawab — "
                        "jawaban tidak diterapkan. Tekan tombol lagi setelah "
                        "pembacaan pulih.")
                    print(
                        "Pembaca konteks gagal sebelum menjawab — jawaban "
                        f"tidak diterapkan: {_samarkan_pesan_galat(e)}")
                    continue
            if aksi == "ulang":
                diulang = True
            if baca_harga is not None:
                pair = getattr(_USULAN_MENUNGGU.get(id_usulan), "pair", None)
                if pair is not None:
                    try:
                        harga = baca_harga(pair)
                        if harga is None:
                            # Hanya cache SEGAR (<= 120 dtk); selain itu
                            # harga hilang dan `on_answer` menolak.
                            harga = portfolio.cached_price(pair, now=now)
                        ctx.prices[pair] = harga
                    except Exception:
                        ctx.prices[pair] = None
            _jawab(
                conn, ctx, aksi, id_usulan, now=now, telegram=telegram,
                transport=transport, shadow=shadow, verdict=verdict,
                revisi=revisi,
            )
        except Exception as e:
            # I2 (review 2026-10-18): offset sudah maju untuk SELURUH batch,
            # jadi satu update beracun tidak boleh membuang saudaranya
            # diam-diam (tombol Setuju/Batal yang hilang tanpa jejak).
            # Dicatat jujur SEKALI — offset maju berarti update ini tidak
            # diantar ulang, sehingga barisnya tidak pernah jadi banjir —
            # lalu antrian dilanjutkan ke update berikutnya.
            print(
                f"Satu update Telegram gagal diproses — dilewati, tidak "
                f"dikirim ulang: {_samarkan_pesan_galat(e)}")
    return diulang


def _polling(conn, ctx, *, now, telegram, transport, shadow, verdict, fetch,
             baca_harga=None, pembaca_konteks=None):
    """Baca `getUpdates`, teruskan ke `on_answer` (satu-satunya konsumennya).

    Mengembalikan True bila ada perintah `/ulang` yang sudah menghasilkan
    usulan pada siklus ini — pemanggil lalu tidak menjalankan `engine.tick`
    lagi.

    Tanpa `fetch` yang disuntikkan, polling dilewati seluruhnya — berarti
    TIDAK ADA jalur keluar jaringan di loop ini.
    """
    if fetch is None:
        return False
    ambil = _ambil_update(telegram, fetch, now=now)
    if ambil is None:      # backoff galat autentikasi: tidak ada yang dibaca
        return False
    updates, offset_baru = ambil
    _simpan_offset(conn, offset_baru)
    return _proses_batch(conn, ctx, updates, now=now, telegram=telegram,
                         transport=transport, shadow=shadow, verdict=verdict,
                         baca_harga=baca_harga,
                         pembaca_konteks=pembaca_konteks)


def tick_cycle(
    conn,
    *,
    now,
    telegram,
    transport=None,
    shadow=True,
    db_state=None,
    exchange_state=None,
    verdict=spread.spread_verdict,
    ctx=None,
    fetch=None,
    baca_harga=None,
    pembaca_konteks=None,
):
    """Satu denyut jantung. Mengembalikan "halted" | "proposed" | "kosong".

    `db_state`/`exchange_state` `None` berarti rekonsiliasi dilewati (dipakai
    tes yang tidak mengujinya); keduanya non-`None` berarti WAJIB diperiksa
    sebelum usulan apa pun.
    """
    ctx = _konteks_kosong() if ctx is None else ctx
    # Sakelar DCA per siklus (Task 15.4): dihitung SEBELUM polling supaya
    # `/ulang` pada siklus ini memakai keputusan bulan yang sama. Jendela
    # yang sudah terbuka boleh retry HANYA untuk pair yang konfirmasinya
    # gagal; pair yang sudah terkirim atau habis percobaannya dilewati.
    terkirim_dca, gagal_dca = _status_kirim_dca(conn, now)
    jam_ok = time.localtime(now).tm_hour < DCA_JAM_TERAKHIR
    habis = {pair for pair, (jumlah, _ts) in gagal_dca.items()
             if pair not in terkirim_dca
             and jumlah >= DCA_MAX_PERCOBAAN_KIRIM}
    retry_tertunda = any(
        pair not in terkirim_dca and jumlah < DCA_MAX_PERCOBAAN_KIRIM
        and terakhir is not None
        and now - terakhir < DCA_RETRY_MIN_INTERVAL_S
        for pair, (jumlah, terakhir) in gagal_dca.items())
    ctx = dataclasses.replace(
        ctx,
        dca_enabled=(
            (dca_window.boleh_usul_dca(conn, now) and (jam_ok or bool(habis)))
            or _boleh_kirim_ulang_dca(conn, now, terkirim_dca, gagal_dca)
        ),
        dca_lewati=(
            set(terkirim_dca)
            | {pair for pair, (jumlah, terakhir) in gagal_dca.items()
               if jumlah >= DCA_MAX_PERCOBAAN_KIRIM
               or (terakhir is not None
                   and now - terakhir < DCA_RETRY_MIN_INTERVAL_S)}
        ),
    )

    # Harga aset di luar target yang gagal dibaca: beri tahu SEKALI per hari
    # dan jangan biarkan aset itu tampil dengan nilai palsu. Denyut TIDAK
    # dihentikan — tidak ada keputusan order (DCA, take-profit, reserve buy,
    # gerbang) yang memakai nilai aset di luar `DEFAULT_TARGET`.
    hilang = sorted(pair for pair, harga in ctx.prices.items()
                    if harga is None and pair not in rules.DEFAULT_TARGET)
    if hilang:
        hari_ini = _hari_lokal(now)
        if hari_ini not in _SUDAH_DIBERITAHU_HARGA:
            _SUDAH_DIBERITAHU_HARGA.add(hari_ini)
            telegram.line(
                "Harga " + ", ".join(hilang) + " tidak terbaca — aset di luar "
                "target disembunyikan dari tampilan. Denyut tetap berjalan.")

    # 1. Rekonsiliasi lebih dulu: buku yang tidak bisa dipercaya tidak boleh
    #    menghasilkan usulan apa pun.
    if db_state is not None and exchange_state is not None:
        selisih = reconcile.mismatches(db_state, exchange_state)
        if selisih:
            for baris in selisih:
                telegram.line(baris)
            shadow_log.record(
                conn,
                types.SimpleNamespace(
                    id=f"rekonsiliasi_halt_{int(now * 1_000_000)}",
                    created_ts=now,
                    kind="HALT",
                    pair=None,
                    amount_idr=None,
                    price_ref=None,
                    rule="rekonsiliasi",
                ),
                "halted",
                "; ".join(selisih),
            )
            return "halted"

    # 2. Metadata pair disegarkan SEBELUM halt izin. Cache ini hanya dibaca
    #    (tidak membuka jalan order apa pun), dan halt izin yang menahan
    #    seluruh denyut tidak boleh ikut membekukan metadata: bot yang
    #    berhenti karena kunci/izin akan menemukan cache basi selamanya
    #    sesudah izin dibereskan (kejadian 2026-11-12). Percobaan gagal
    #    dibatasi `pair_meta.RETRY_S`; kegagalan dicatat dan pengguna
    #    diberitahu SEKALI per hari hanya bila cache benar-benar basi.
    try:
        pair_meta.refresh(conn, now=now)
    except Exception as e:
        _catat_denyut_dilewatkan(f"metadata pair gagal disegarkan: {e}")
        umur = pair_meta.umur_tertua(conn, now=now)
        hari_ini = time.strftime("%Y-%m-%d", time.localtime(now))
        if ((umur is None or umur > pair_meta.MAX_AGE_S)
                and hari_ini not in _SUDAH_DIBERITAHU_METADATA):
            _SUDAH_DIBERITAHU_METADATA.add(hari_ini)
            telegram.line(
                "Metadata pair (presisi qty / minimum bursa) basi atau "
                "belum tersedia — order baru ditahan sampai penyegaran "
                "berhasil. Periksa koneksi ke indodax.com.")

    # 3. Izin bursa dibaca ulang tepat sebelum ada keputusan (spec §6.6).
    #    Tanpa transport tidak ada kunci yang bisa dibaca — dan tanpa kunci
    #    tidak ada order yang bisa jalan.
    if transport is not None:
        izin = safeguards.permissions_ok(
            transport.can_trade, transport.can_withdraw, switch_on=not shadow
        )
        if izin is not None:
            telegram.line(izin)
            return "halted"

    # 4. Polling Telegram — wajib, bukan pelengkap (F6).
    diulang = _polling(conn, ctx, now=now, telegram=telegram,
                       transport=transport, shadow=shadow, verdict=verdict,
                       fetch=fetch, baca_harga=baca_harga,
                       pembaca_konteks=pembaca_konteks)

    # 5. Pengingat pasif DCA, maksimal satu pesan per slot jam (T-13.1).
    if dca_window.should_remind(conn, now):
        slot = (time.strftime("%Y-%m-%d", time.localtime(now)),
                time.localtime(now).tm_hour)
        if slot not in _SLOT_PENGINGAT:
            _SLOT_PENGINGAT.add(slot)
            sisa = dca_window.dca_state(conn, now)[1] / 3600.0
            telegram.line(
                "Pengingat: konfirmasi DCA bulan ini belum dijawab. "
                f"Sisa waktu {sisa:.1f} jam — tekan Setuju atau Batal bulan ini."
            )

    # 6. Satu tick engine — DILEWATI kalau `/ulang` sudah mengusulkan pada
    #    siklus ini. Usulan ulang memakai aksi dan `now` yang sama persis
    #    dengan tick ini, jadi `proposal.new_proposal` akan menghasilkan id
    #    kembar dan `shadow_log` menimpanya (atau gagal IntegrityError).
    #    Metadata sudah disegarkan di langkah 2 — di sini engine hanya membaca.
    try:
        usulan = [] if diulang else engine.tick(
            conn, ctx, now=now, telegram=telegram, shadow=shadow,
            verdict=verdict,
        )
    except ValueError:
        # Aksi tanpa provenance = BERHENTI SEGERA, tanpa diulang. Satu-satunya
        # arah aman: nama aturan yang hilang berarti keputusan tidak bisa
        # ditelusuri, dan menebak berarti mempertahankan angka yang salah.
        # Yang TIDAK dijamin "diam": bila ValueError datang di TENGAH daftar
        # aksi, pesan konfirmasi untuk aksi SEBELUMNYA mungkin sudah terkirim
        # ke pengguna — tetapi `usulan` tidak pernah dikembalikan, jadi
        # `_ingat` tidak mendaftarkannya dan tombolnya inert lewat jalur
        # "id tak dikenal" (F46). Konsekuensi jendela DCA: `_catat_dca` tidak
        # jalan, bulan DCA TETAP TERBUKA — percobaan belum dihitung habis dan
        # diusulkan ulang pada siklus sehat berikutnya (selama jendela 24 jam
        # masih berakhir di bulan yang sama). Pesan konfirmasi yatim itu
        # tidak mengunci apa pun dan tidak bisa memicu order.
        return "halted"
    _ingat(usulan)
    # Pencatatan jendela DCA (Task 15.4): buka bila usulan DCA benar-benar
    # terkirim, tutup bulan bila percobaan habis tanpa hasil. Guard
    # `not diulang`: siklus `/ulang` sudah mencatat di `apply_ulang` —
    # dua pencatatan untuk satu keputusan bulan adalah bug, bukan pengaman.
    hasil_catat = None
    if ctx.dca_enabled and not diulang:
        hasil_catat = _catat_dca(
            conn, now, usulan,
            tunda=(getattr(ctx, "dca_retry", False) or retry_tertunda
                   or getattr(ctx, "dca_saldo_kurang", False)))
    if not getattr(ctx, "dca_saldo_kurang", False):
        _beritahu_dca_berakhir(conn, now, telegram, gagal_dca,
                               baru_skip=hasil_catat == "skip")
    _beritahu_retry_habis(conn, now, telegram, terkirim_dca, gagal_dca)

    # 7. Sakelar mode bayangan.
    apply_shadow_gate(conn, now=now)

    return "proposed" if usulan else "kosong"


def apply_ulang(conn, ctx, *, now, telegram, shadow,
                verdict=spread.spread_verdict):
    """/ulang: usulkan SEKARANG dengan data saat ini juga.

    Hanya pengguna yang memicu (spec §7.1 butir 4) — tidak menunggu denyut
    jantung berikutnya. Angka diambil dari konteks SEKARANG; usulan lama tidak
    pernah dipakai ulang, karena angkanya bisa sudah bergeser.
    """
    usulan = _ingat(engine.tick(
        conn, ctx, now=now, telegram=telegram, shadow=shadow, verdict=verdict,
    ))
    # `/ulang` pada jam usul tetap mencatat jendela (Task 15.4): usulan
    # terkirim lewat sini = jendela resmi dibuka; nol terkirim = percobaan
    # habis. `tick_cycle` tidak ikut mencatat (guard `not diulang`).
    if ctx.dca_enabled:
        _catat_dca(conn, now, usulan,
                   tunda=getattr(ctx, "dca_retry", False))
    return usulan


def _env(kunci):
    """Ambil dari environment atau `.env` — bukan kunci dagang, hanya token."""
    return scheduler._env(kunci)


def _kunci_satu_instance(jalur=None):
    """Kunci file untuk SATU instance bot produksi (I5 review 2026-10-18).

    `MultipleInstances IgnoreNew` dan ulangan VBS hanya menjaga instance yang
    LAHIR dari Task Scheduler; `python main_loop.py` manual atau dobel-klik
    `run_v1.bat` tetap bisa membuat KONSUMEN getUpdates kedua — pemrosesan
    tombol dobel dan perebutan `portfolio.db`.

    Mengunci SATU byte lewat `msvcrt.locking`; Windows melepas kunci begitu
    handle atau proses pemegang mati, jadi tidak pernah ada kunci basi
    setelah bot crash (berbeda dengan kunci berbasis keberadaan berkas).

    Mengembalikan handle yang WAJIB tetap terbuka selama proses hidup, atau
    None bila kunci sudah dipegang — gagal ke arah aman: tanpa kunci, lebih
    baik bot kedua menolak diri daripada dua pembaca getUpdates.
    """
    import msvcrt

    if jalur is None:
        jalur = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "main_loop.lock")
    handle = None
    try:
        handle = open(jalur, "a+b")
        if os.path.getsize(jalur) == 0:
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if handle is not None:
            handle.close()
        return None
    return handle


def main(argv=None, **kwargs):
    """Jalankan loop dengan stdout bercap waktu WIB (isi `v1_loop.log`).

    Pembungkus tipis: `_main` tetap seluruh logika. Yang ditambahkan hanya cap
    waktu per baris dan pemulihan `sys.stdout` apa pun hasilnya.

    `jam_cetak` (opsional) = sumber waktu cap log, bawaan `time.time`.
    Dipisah dari `jam` supaya pencatatan tidak memakan jam yang disuntik tes.
    """
    jam_cetak = kwargs.pop("jam_cetak", time.time)
    asli = sys.stdout
    sys.stdout = _CetakBerjam(asli, jam_cetak)
    try:
        return _main(argv, **kwargs)
    finally:
        try:
            sys.stdout.flush()
        finally:
            sys.stdout = asli


def _main(argv=None, *, conn=None, db_path=None, telegram=None,
          transport_factory=None, ctx=None, fetch=None, jam=None, tidur=None,
          pembaca_konteks=None, baca_harga=None):
    """Jalankan loop. 0 = berhenti wajar, 1 = gagal (konsisten `scheduler.py`).

    `transport_factory` adalah satu-satunya jalan yang MUNGKIN menyentuh
    bursa, dan hanya dipanggil bila sakelar sudah menyala. Tanpa factories
    apa pun, `main()` tidak membangun objek apa pun ke bursa, mencetak satu
    baris berbahasa Indonesia yang jelas, lalu melanjutkan dalam mode bayangan.

    Dua irama (rancangan periode shadow): setiap bangun (`POLL_GAP_S`)
    membaca tombol Telegram; denyut penuh (`HEARTBEAT_S`) menjalankan
    `tick_cycle` setelah konteks pasar dibaca ulang lewat `pembaca_konteks`
    (bawaan `None` = konteks disuntik pemanggil, perilaku lama persis).
    `baca_harga(pair)` menyegarkan harga pasangan yang sedang dijawab.
    Pembaca gagal = denyut dilewati dan polling ditahan: tanpa bacaan yang
    utuh tidak ada satu pun keputusan (ragu = tidak kirim).
    """
    parser = argparse.ArgumentParser(
        prog="main_loop",
        description="Loop utama bot semi-otomatis (mode bayangan secara bawaan).")
    parser.add_argument("--siklus", type=int, default=None,
                        help="Jumlah bangun tidur (tiap POLL_GAP_S) lalu "
                             "keluar; denyut penuh jalan tiap HEARTBEAT_S "
                             "(bawaan: terus).")
    args = parser.parse_args(argv)

    # ABSOLUT — lihat catatan main_loop.ERRORS_LOG (cacat 2026-10-20).
    logging.basicConfig(
        filename=ERRORS_LOG,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    jam = time.time if jam is None else jam
    tidur = time.sleep if tidur is None else tidur

    # Penjaga zona: seluruh jadwal (09.00, DND, 21.00) adalah WIB. Mesin
    # dengan offset lain menggeser semuanya diam-diam -> tolak start, sebelum
    # basis data disentuh dan sebelum ada satu pun usulan.
    offset = _offset_zona_lokal(jam())
    if offset != ZONA_OFFSET_DETIK:
        print(
            f"Bot berhenti: zona waktu mesin {_teks_offset(offset)}, bukan "
            "+07:00 (WIB). Atur zona mesin ke Asia/Jakarta — jam kirim DCA, "
            "DND, dan batas 21.00 bergantung padanya. Tidak ada order yang "
            "dikirim."
        )
        return 1

    conn_milik = False

    try:
        if conn is None:
            conn = portfolio_store.connect(db_path or portfolio_store.DB_FILE)
            portfolio_store.init_db(conn)
            conn_milik = True
        dca_window.ensure_bot_state(conn)
    except Exception as e:
        print(f"Gagal menyiapkan basis data: {_samarkan_pesan_galat(e)}")
        return 1

    try:
        if telegram is None:
            token = _env("TELEGRAM_TOKEN")
            chat = _env("TELEGRAM_CHAT_ID")
            if not token or not chat:
                print(
                    "Telegram belum diset (TELEGRAM_TOKEN/TELEGRAM_CHAT_ID) — "
                    "bot berhenti: gerbang adalah satu-satunya jalan keluar."
                )
                return 1
            telegram = telegram_gate.Telegram(token, chat)

        reset_state()
        _muat_usulan(conn, now=jam())
        _muat_offset(conn)
        transport = None
        # Banner status dicetak SATU KALI PER KEADAAN — bukan tiap bangun
        # (irama 15 detik = ±5.760 baris/hari yang menelan baris kegagalan
        # asli), dan bukan sekali-per-proses yang membeku: perubahan status
        # wajib tercatat (M6). Penanda direset saat status BERUBAH.
        sudah_cetak_status = False
        sudah_cetak_pabrik = False
        status_lama = None

        if ctx is None:
            ctx = _konteks_kosong()

        siklus = 0
        denyut_terakhir = None
        # Tanpa pembaca (konteks disuntik pemanggil) konteks selalu dianggap
        # sah; dengan pembaca, status inilah yang menahan polling sampai
        # bacaan pasar berikutnya berhasil — tidak ada keputusan di atas data
        # yang gagal dibaca.
        konteks_baik = pembaca_konteks is None
        sapa_terkirim = False
        gagal_pembaca = False
        gagal_polling = False
        gagal_denyut = False
        # Penghitung percobaan gagal berturut-turut per penanda — direset
        # saat pemulihan (lihat ULANG_LOG_KEGAGALAN).
        gagal_pembaca_coba = 0
        gagal_polling_coba = 0
        gagal_denyut_coba = 0

        def _sapa_awal():
            """Sapa awal SETELAH poll pertama sukses (M2 review 2026-10-18).

            Klaim "polling Telegram aktif" yang dicetak sebelum getUpdates
            pertama adalah bukti palsu: token mati pun akan terlihat sehat.
            Boolean hasil `telegram.line` dicek — kegagalan pengiriman
            dicatat jujur ke log, bukan diabaikan. Kata sapa diturunkan dari
            mode `shadow` yang sedang berjalan, bukan hardcode.
            """
            nonlocal sapa_terkirim
            if fetch is None or sapa_terkirim:
                return
            sapa_terkirim = True
            if _sapa_boleh(conn, sekarang):
                _catat_sapa(conn, sekarang)
                mode = ("mode bayangan, tanpa order yang bisa dikirim" if shadow
                        else "eksekusi menyala — pantau dengan teliti")
                print(f"Bot siap: polling Telegram aktif — {mode}.")
                pesan = f"Bot semi-otomatis berjalan — {mode}."
                terkirim = False
                for percobaan in range(1, SAPA_ULANG_MAKS + 1):
                    if telegram.line(pesan):
                        terkirim = True
                        break
                    if percobaan < SAPA_ULANG_MAKS:
                        tidur(SAPA_JEDA_ULANG_S)
                if not terkirim:
                    print(f"Pesan sapa awal tidak terkirim setelah "
                          f"{SAPA_ULANG_MAKS} percobaan — polling tetap jalan.")
            _status_harian()

        def _status_harian():
            """Status sekali per hari lokal — setelah poll pertama sukses.

            Isinya mode, hari bayangan, kesegaran metadata, status DCA bulan
            ini, dan commit yang berjalan. Dedupe per tanggal lokal yang
            DISIMPAN DI `bot_state.status_terakhir_hari`: restart pada hari
            yang sama tidak mengirim ulang (penanda memori akan hilang tiap
            proses). Tanggal ditulis SEBELUM kirim: satu percobaan per hari,
            bukan satu percobaan tiap denyut saat Telegram sedang mati.
            Sebelum poll pertama sukses tidak ada klaim apa pun (M2).
            """
            if not sapa_terkirim:
                return
            hari = _hari_lokal(sekarang)
            dca_window.ensure_bot_state(conn)
            terakhir = conn.execute(
                "SELECT status_terakhir_hari FROM bot_state WHERE id = 1"
            ).fetchone()[0]
            if terakhir == hari:
                return
            conn.execute(
                "UPDATE bot_state SET status_terakhir_hari = ? WHERE id = 1",
                (hari,))
            conn.commit()
            if not telegram.line(status_ringkas(
                    conn, now=sekarang, shadow=shadow,
                    transport=transport)):
                print("Status harian tidak terkirim — polling tetap jalan.")

        while args.siklus is None or siklus < args.siklus:
            sekarang = jam()
            keadaan = load_state(conn)
            shadow = not keadaan["v1_enabled"]

            # M6 (review 2026-10-18): penanda banner direset HANYA saat status
            # benar-benar berubah — persis seperti yang diklaim komentar dan
            # docstring ("dicetak saat statusnya BERUBAH"). Tanpa ini penanda
            # membeku di keadaan pertama dan transisi mode bayangan -> v1 ->
            # bayangan tidak pernah tercatat lagi di log.
            status = ("bayangan" if shadow else
                      ("tanpa-pabrik" if transport_factory is None
                       else "menyala"))
            if status != status_lama:
                status_lama = status
                sudah_cetak_status = False
                sudah_cetak_pabrik = False

            if shadow:
                transport = None
                if not sudah_cetak_status:
                    sudah_cetak_status = True
                    print(
                        "Eksekusi belum dinyalakan: berjalan dalam mode bayangan, "
                        "tidak ada order yang bisa dikirim."
                    )
            elif transport_factory is None:
                transport = None
                if not sudah_cetak_pabrik:
                    sudah_cetak_pabrik = True
                    print(
                        "Eksekusi belum dinyalakan: tidak ada pabrik transport, "
                        "tidak ada order yang bisa dikirim."
                    )
            else:
                if transport is None:
                    transport = transport_factory()

            # Sapa awal TIDAK lagi dicetak di sini: klaim "polling aktif"
            # sebelum getUpdates pertama adalah bukti palsu (M2 review).
            # Lihat `_sapa_awal` — dipanggil hanya setelah poll sukses.

            if should_run_habitual(sekarang):
                denyut_penuh = (
                    denyut_terakhir is None
                    or (sekarang - denyut_terakhir) >= HEARTBEAT_S
                )
                # Ambil batch TANPA memajukan offset untuk memutuskan irama
                # (review 2026-10-18, C1/I3/I4): batch berisi `/ulang` = bangun
                # ini dinaikkan jadi denyut penuh — konteks dibaca ulang, sakelar
                # DCA dihitung `tick_cycle`, dan `apply_shadow_gate` menutup
                # denyut yang sama, sehingga penolakan antar-denyut tidak pernah
                # lolos ke hari berikutnya (C1). Syarat TANPA transport (I4):
                # jawaban tidak boleh diproses tanpa rekonsiliasi dan cek izin —
                # saat transport ada, semua jawaban ditahan ke denyut penuh.
                # Offset hanya maju bila batch yang SAMA benar-benar diproses
                # sebagai polling antar-denyut.
                ambil = None
                promosi = False
                if (not denyut_penuh and konteks_baik
                        and transport is None and fetch is not None):
                    try:
                        ambil = _ambil_update(telegram, fetch, now=sekarang)
                        if ambil is not None:
                            promosi = _ada_perintah_ulang(ambil[0])
                            if gagal_polling:
                                print(
                                    "Polling Telegram sudah pulih — jalan kembali.")
                            gagal_polling = False
                            gagal_polling_coba = 0
                            _sapa_awal()
                    except Exception as e:
                        # Sebabnya sudah dicetak `_catat_galat_telegram`
                        # (sekali per jenis per jam); di sini cukup keadaan.
                        gagal_polling_coba += 1
                        gagal_polling = True
                        _catat_denyut(conn, now=sekarang, galat=e)
                if denyut_penuh or promosi:
                    if pembaca_konteks is None:
                        konteks_baik = True
                    else:
                        try:
                            ctx = pembaca_konteks(conn)
                            if gagal_pembaca:
                                print(
                                    "Pembaca pasar sudah pulih — denyut jalan "
                                    "kembali.")
                            gagal_pembaca = False
                            gagal_pembaca_coba = 0
                            konteks_baik = True
                        except Exception as e:
                            konteks_baik = False
                            # Cetak di percobaan pertama lalu tiap ke-20
                            # percobaan berturut-turut — bukan banjir log tiap
                            # 15 detik, tapi juga bukan sunyi selamanya bila
                            # kegagalan ternyata permanen (I1 review).
                            gagal_pembaca_coba += 1
                            if (not gagal_pembaca or
                                    gagal_pembaca_coba % ULANG_LOG_KEGAGALAN == 0):
                                gagal_pembaca = True
                                _pesan_gagal = (
                                    "Pembaca pasar gagal — denyut dilewatkan, "
                                    f"tidak ada keputusan: "
                                    f"{_samarkan_pesan_galat(e)}"
                                )
                                print(_pesan_gagal)
                                _catat_denyut_dilewatkan(_pesan_gagal)
                            _catat_denyut(conn, now=sekarang, galat=e)
                    if konteks_baik:
                        try:
                            tick_cycle(conn, now=sekarang, telegram=telegram,
                                       transport=transport, shadow=shadow,
                                       ctx=ctx, fetch=fetch,
                                       baca_harga=baca_harga,
                                       pembaca_konteks=pembaca_konteks)
                        except Exception as e:
                            # Galat denyut = denyut dilewati, dicoba ulang pada
                            # bangun berikutnya (15 dtk) — proses TIDAK boleh
                            # mati (kasus nyata 2026-10-18: timeout Telegram
                            # membunuh bot dan G5 berhenti menghitung). Cetak
                            # pertama + tiap ke-20 percobaan berturut-turut,
                            # sama seperti penjaga pembaca dan polling (I1).
                            gagal_denyut_coba += 1
                            if (not gagal_denyut or
                                    gagal_denyut_coba % ULANG_LOG_KEGAGALAN == 0):
                                gagal_denyut = True
                                _pesan_gagal = (
                                    f"Denyut penuh gagal — dilewati, dicoba "
                                    f"lagi: {_samarkan_pesan_galat(e)}"
                                )
                                # Galat Telegram sudah punya barisnya sendiri.
                                if not getattr(e, "_telegram_dicatat", False):
                                    print(_pesan_gagal)
                                _catat_denyut_dilewatkan(_pesan_gagal)
                            _catat_denyut(conn, now=sekarang, galat=e)
                        else:
                            if gagal_denyut:
                                print(
                                    "Denyut penuh sudah pulih — jalan kembali.")
                            gagal_denyut = False
                            gagal_denyut_coba = 0
                            _sapa_awal()
                            _catat_denyut(conn, now=sekarang)
                            # Status harian juga dicek di tiap denyut SUKSES:
                            # proses yang berjalan berhari-hari harus mengirim
                            # status baru saat tanggal lokalnya berganti
                            # (dedupe tanggal di `bot_state`).
                            _status_harian()
                            # Hanya denyut SUKSES yang memperbarui penanda: bila
                            # gagal, denyut berikutnya adalah percobaan ulang
                            # (15 dtk kemudian), bukan tidur 300 dtk.
                            denyut_terakhir = sekarang
                elif konteks_baik and ambil is not None:
                    # Antar denyut: cukup baca tombol — jendela 90 detik
                    # tidak menunggu denyut penuh berikutnya. Kegagalan jaringan
                    # dilewati sekali lalu dicoba lagi pada bangun berikutnya;
                    # offset tidak pernah maju saat gagal, jadi antrian update
                    # di server Telegram tidak hilang.
                    updates, offset_baru = ambil
                    try:
                        _simpan_offset(conn, offset_baru)
                        _proses_batch(conn, ctx, updates, now=sekarang,
                                      telegram=telegram, transport=transport,
                                      shadow=shadow,
                                      verdict=spread.spread_verdict,
                                      baca_harga=baca_harga,
                                      pembaca_konteks=pembaca_konteks)
                        if updates:
                            # Sabuk C1: batch berisi apa pun = periksa penolakan
                            # hari ini juga. Batch kosong mustahil meninggalkan
                            # jejak, jadi denyut lama tidak terganggu.
                            apply_shadow_gate(conn, now=sekarang)
                        gagal_polling = False
                    except Exception as e:
                        gagal_polling_coba += 1
                        if (not gagal_polling or
                                gagal_polling_coba % ULANG_LOG_KEGAGALAN == 0):
                            gagal_polling = True
                            print(f"Polling Telegram gagal — dilewati: {_samarkan_pesan_galat(e)}")
                        _catat_denyut(conn, now=sekarang, galat=e)
            # Tidur selalu, termasuk di luar jam aktif: hitungan denyut harus tetap
            # berjalan supaya perubahan jam lokal langsung diikuti.
            tidur(POLL_GAP_S)
            siklus += 1
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"Gagal menjalankan loop: {_samarkan_pesan_galat(e)}")
        return 1
    finally:
        if conn_milik and conn is not None:
            conn.close()


if __name__ == "__main__":
    # Jalur produksi — satu-satunya tempat fetch Telegram, pembaca pasar, dan
    # penyegar harga NYATA disuntikkan. `main()` dipanggil tes tanpa ketiganya
    # supaya tidak satu pun tes bisa menyentuh jaringan (conftest tetap jaga).
    # Kunci instance dulu (I5 review): tanpa ini, menjalankan berkas ini dua
    # kali membuat dua konsumen getUpdates — tombol diproses dobel dan kedua
    # proses berebut `portfolio.db`. Kunci dilepas OS bila proses pemegang
    # mati, jadi crash tidak meninggalkan penghalang.
    kunci = _kunci_satu_instance()
    if kunci is None:
        print(
            "Bot sudah berjalan di proses lain — instance kedua ditutup "
            "(satu-satunya pembaca getUpdates)."
        )
        raise SystemExit(1)
    # transport_factory disuntik di sini, dan HANYA di sini. Pabriknya hanya
    # dipanggil `main()` bila sakelar v1 sudah menyala (7 hari bayangan bersih);
    # selama mode bayangan transport tetap None dan `permissions_ok` menghentikan
    # apa pun sebelum menyentuh bursa (spec 6.6/6.7, review 2026-10-18).
    raise SystemExit(main(
        fetch=telegram_gate.live_fetch,
        pembaca_konteks=baca_konteks,
        baca_harga=portfolio.fetch_price,
        transport_factory=indodax_transport.transport_factory,
    ))
