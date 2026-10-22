"""
Penyimpanan pemantauan per HARI: satu file SQLite per tanggal.

Keputusan 2026-10-22: `trades.db` (58 MB, ±20.000 baris/hari) dipecah jadi
`trades_YYYY-MM-DD.db` supaya:
- data lama TIDAK hilang — semua hari tetap ada, file lama jadi arsip
  `trades_arsip.db`,
- data bisa diambil per tanggal (uji strategi, pantau per hari),
- file hari ini kecil, jadi SQLite cepat.

Penulis (monitor.py): `koneksi_hari(tulis=True)` = file hari ini, wajib
berganti file saat tanggal berpindah.

Pembaca:
- `koneksi_hari(tanggal)`  satu hari (yang belum ada = FileNotFoundError)
- `terakhir(...)`          baris terbaru, mundur hari demi hari
- `buka(awal, akhir)`      rentang hari: satu hari = file langsung,
                           lebih dari satu hari = salinan di memori
- `daftar_hari()`          tanggal yang punya file

Migrasi file lama (sekali jalan, SETELAH monitor pindah ke file harian):
    python trades_store.py --migrasi

Direktori bisa dipindah lewat env `TRADES_DB_DIR` (dipakai tes).
"""

import os
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PREFIX, _SUFFIX = "trades_", ".db"

# {db} diisi "" (koneksi sendiri) atau "dst." (migrasi lewat ATTACH).
_SKEMA_TPL = (
    "CREATE TABLE IF NOT EXISTS {db}trades ("
    "pair TEXT, tid INTEGER, date INTEGER, price REAL, amount REAL,"
    " type TEXT, fetched_at TEXT, PRIMARY KEY (pair, tid))",
    "CREATE TABLE IF NOT EXISTS {db}depth_snapshots ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, date INTEGER,"
    " bid_vol REAL, ask_vol REAL, imbalance REAL, fetched_at TEXT)",
)

# Kolom yang disalin migrasi. `id` depth_snapshots sengaja DITINGGALKAN:
# AUTOINCREMENT tiap file mulai dari 1, jadi mempertahankan id lama pasti
# bentrok dengan baris yang sudah ditulis monitor di file harian baru.
_KOLOM = {
    "trades": ("pair", "tid", "date", "price", "amount", "type", "fetched_at"),
    "depth_snapshots": ("pair", "date", "bid_vol", "ask_vol", "imbalance", "fetched_at"),
}


def db_dir():
    return os.environ.get("TRADES_DB_DIR") or BASE_DIR


def hari_ini(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


def path_hari(tanggal):
    return os.path.join(db_dir(), _PREFIX + tanggal + _SUFFIX)


def path_arsip():
    return os.path.join(db_dir(), "trades_arsip.db")


def daftar_hari():
    """Tanggal yang punya file, menaik (['2026-10-08', ...])."""
    hasil = []
    for nama in os.listdir(db_dir()):
        if nama.startswith(_PREFIX) and nama.endswith(_SUFFIX):
            t = nama[len(_PREFIX):-len(_SUFFIX)]
            if len(t) == 10 and t[4] == "-" and t[7] == "-":
                hasil.append(t)
    return sorted(hasil)


def koneksi_hari(tanggal=None, *, tulis=False):
    """Koneksi ke file satu hari; `tanggal=None` = hari ini.

    Mode baca memastikan file benar-benar ada — tanpa itu `sqlite3.connect`
    diam-diam membuat file kosong dan `daftar_hari()` jadi kotor.
    """
    path = path_hari(tanggal or hari_ini())
    if not tulis and not os.path.exists(path):
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    if tulis:
        for sql in _SKEMA_TPL:
            conn.execute(sql.format(db=""))
        conn.commit()
    return conn


def terakhir(tabel, kolom, pair, *, limit, urut="date DESC, id DESC"):
    """Baris terbaru untuk `pair`, mundur hari demi hari sampai `limit` penuh.

    `urut` harus DESC; `trades` tidak punya kolom `id`, jadi pemanggilnya
    mengirim `urut="date DESC, tid DESC"`.
    """
    hasil = []
    for tanggal in reversed(daftar_hari()):
        if len(hasil) >= limit:
            break
        conn = koneksi_hari(tanggal)
        try:
            hasil.extend(conn.execute(
                "SELECT %s FROM %s WHERE pair = ? ORDER BY %s LIMIT ?"
                % (kolom, tabel, urut), (pair, limit - len(hasil))).fetchall())
        finally:
            conn.close()
    return hasil


def rentang(teks):
    """'2026-10-22' atau '2026-10-18..2026-10-22' -> (awal, akhir)."""
    if not teks:
        return None, None
    if ".." in teks:
        a, b = teks.split("..", 1)
        return a.strip() or None, b.strip() or None
    t = teks.strip()
    return t, t


def _pilih(awal, akhir):
    return [t for t in daftar_hari()
            if (awal is None or t >= awal) and (akhir is None or t <= akhir)]


def gabung(awal=None, akhir=None):
    """Satu koneksi memori berisi semua baris hari `awal`..`akhir`.

    Menyalin baris (bukan ATTACH) supaya query lama yang menulis nama tabel
    tanpa skema tetap jalan berapa pun jumlah file yang digabung.
    """
    conn = sqlite3.connect(":memory:")
    for sql in _SKEMA_TPL:
        conn.execute(sql.format(db=""))
    for tanggal in _pilih(awal, akhir):
        src = koneksi_hari(tanggal)
        try:
            for tabel, kolom in _KOLOM.items():
                rows = src.execute(
                    "SELECT %s FROM %s" % (", ".join(kolom), tabel)).fetchall()
                if rows:
                    conn.executemany(
                        "INSERT OR IGNORE INTO %s (%s) VALUES (%s)"
                        % (tabel, ", ".join(kolom), ",".join("?" * len(kolom))),
                        rows)
        except sqlite3.Error:
            # file hari itu korup/parsial: lewati, jangan gagalkan seluruh rentang
            continue
        finally:
            src.close()
    conn.commit()
    return conn


def buka(awal=None, akhir=None):
    """Koneksi baca rentang tanggal.

    Satu hari = file hari itu langsung (cepat, ikut terisi live);
    lebih dari satu hari atau tanpa batas = gabungan di memori.
    """
    pilih = _pilih(awal, akhir)
    if len(pilih) == 1:
        return koneksi_hari(pilih[0])
    return gabung(awal, akhir)


def _batas_hari(tanggal):
    """(mulai, selesai) epoch detik untuk satu tanggal, zona waktu lokal."""
    try:
        awal = int(time.mktime(time.strptime(tanggal, "%Y-%m-%d")))
    except (OverflowError, ValueError):
        # Windows menolak waktu pra-1970 (mktime -> OverflowError): batas
        # bawah 0 (epoch UTC). Baris tanggal 0-an tetap masuk hari itu.
        awal = 0
    return awal, awal + 86400


def migrasi(sumber=None):
    """Sekali jalan: pecah `trades.db` lama jadi file per hari.

    Jalankan SETELAH monitor pindah ke file harian (restart task
    IndodaxMonitor), supaya `trades.db` sudah berhenti berubah dan tidak ada
    baris yang tertinggal. Baris `trades` disalin dengan INSERT OR IGNORE
    (PK pair+tid menangkap dobel); `depth_snapshots` dihapus per tanggal
    dulu supaya migrasi ulang setelah berhenti di tengah tidak menggandakan.

    File sumber tidak dihapus — diganti nama jadi `trades_arsip.db`, sekaligus
    penanda migrasi sudah jalan (migrasi kedua otomatis batal).
    """
    sumber = sumber or os.path.join(db_dir(), "trades.db")
    if not os.path.exists(sumber):
        print("Tidak ada %s — tidak ada yang dimigrasi." % sumber)
        return
    conn = sqlite3.connect(sumber)
    try:
        tanggal = sorted({t for (t,) in conn.execute(
            # 'unixepoch' WAJIB: tanpa itu SQLite menganggap angka mentah
            # sebagai nomor Julian day -> hasilnya NULL/aneh, bukan 2026.
            "SELECT DISTINCT strftime('%Y-%m-%d', date, 'unixepoch', 'localtime') "
            "FROM trades WHERE date IS NOT NULL UNION "
            "SELECT DISTINCT strftime('%Y-%m-%d', date, 'unixepoch', 'localtime') "
            "FROM depth_snapshots WHERE date IS NOT NULL") if t})
        print("%s -> %d tanggal" % (os.path.basename(sumber), len(tanggal)))
        for t in tanggal:
            conn.execute("ATTACH DATABASE ? AS dst", (path_hari(t),))
            try:
                for sql in _SKEMA_TPL:
                    conn.execute(sql.format(db="dst."))
                lo, hi = _batas_hari(t)
                n_hapus = conn.execute(
                    "DELETE FROM dst.depth_snapshots WHERE date >= ? AND date < ?",
                    (lo, hi)).rowcount
                jml = {}
                for tabel, kolom in _KOLOM.items():
                    jml[tabel] = conn.execute(
                        "INSERT OR IGNORE INTO dst.%s (%s) "
                        "SELECT %s FROM %s WHERE date >= ? AND date < ?"
                        % (tabel, ", ".join(kolom), ", ".join(kolom), tabel),
                        (lo, hi)).rowcount
                conn.commit()
                print("  %s  trades +%d  depth +%d%s"
                      % (t, jml["trades"], jml["depth_snapshots"],
                         " (hapus %d baris lama)" % n_hapus if n_hapus else ""))
            finally:
                conn.execute("DETACH DATABASE dst")
        if tanggal:
            lo0 = _batas_hari(tanggal[0])[0]
            hi1 = _batas_hari(tanggal[-1])[1]
            sisa = conn.execute(
                "SELECT (SELECT COUNT(*) FROM trades "
                "        WHERE date IS NULL OR date < ? OR date >= ?) + "
                "       (SELECT COUNT(*) FROM depth_snapshots "
                "        WHERE date IS NULL OR date < ? OR date >= ?)",
                (lo0, hi1, lo0, hi1)).fetchone()[0]
            if sisa:
                print("Peringatan: %d baris di luar rentang tanggal mana pun "
                      "(NULL/tanggal aneh) — tetap utuh di arsip, tidak "
                      "dipindah ke file harian." % sisa)
    finally:
        conn.close()

    # Ganti nama setelah koneksi tertutup (Windows menolak rename file terbuka).
    arsip = path_arsip()
    if os.path.exists(arsip):
        print("Peringatan: %s sudah ada, %s tidak diganti nama (migrasi tetap beres)."
              % (arsip, sumber))
    else:
        os.rename(sumber, arsip)
        print("Selesai. Arsip asli (boleh dihapus setelah dicek): %s" % arsip)


if __name__ == "__main__":
    import sys

    if "--migrasi" in sys.argv:
        migrasi()
    else:
        hari = daftar_hari()
        print("Hari tersedia:", ", ".join(hari) or "(belum ada)")
        print("Hari ini     :", hari_ini(), "->", path_hari(hari_ini()))
