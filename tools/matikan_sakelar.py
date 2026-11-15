"""Alat satu-kali: matikan sakelar eksekusi bot — otomasi docs/RUNBOOK_SAKELAR.md.

Dijalankan MANUAL oleh pemilik bot di PowerShell Administrator. Tanpa argumen
alat ini DRY-RUN: hanya mencetak rencana dan memeriksa prasyarat; tidak ada
tugas, proses, DB, atau sakelar yang disentuh. Perubahan nyata hanya dengan
`--jalankan`, dan tiap fase meminta konfirmasi ketik LANJUT (kecuali `--ya`).

Batas keras yang dijaga alat ini:

- sakelar hanya dimatikan lewat jalur resmi `main_loop.set_shadow_day(conn, 0)`
  (persis RUNBOOK §e1); alat tidak pernah menulis `v1_enabled = 1`, tidak
  memanggil `aktifkan_live`, dan tidak menyentuh `.env` / izin kunci API;
- tugas yang disentuh hanya `IndodaxV1Shadow` — `IndodaxDashboard` dan proses
  dashboard tidak pernah disebut;
- cadangan dibuat lewat API backup `sqlite3` (`src.backup(dst)`) ke folder DI
  LUAR repo, lalu diverifikasi `PRAGMA integrity_check` + jumlah baris;
- gagal di fase mana pun = berhenti total. Bila kegagalan terjadi setelah fase
  c, bot dibiarkan MATI dan tugas tetap nonaktif; cadangan TIDAK dipulihkan
  otomatis karena cadangan itu memuat `v1_enabled = 1`.

Subprocess, jam, tidur, dan konfirmasi disuntikkan supaya bisa diuji tanpa
Windows, tanpa proses nyata, dan tanpa `portfolio.db` asli.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

import main_loop

NAMA_TUGAS = "IndodaxV1Shadow"
JEDA_PREFLIGHT_S = 60
TUNGGU_MENIT_BAWAAN = 20
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SKRIP_PROSES = (
    "Get-CimInstance Win32_Process -Filter \"Name='wscript.exe' or "
    "Name='pythonw.exe' or Name='python.exe'\" | "
    "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
    "ConvertTo-Json -Compress"
)

_PEMULIHAN = """\
Keadaan sekarang: bot DIBIARKAN MATI, tugas IndodaxV1Shadow TETAP NONAKTIF.
Cadangan TIDAK dipulihkan otomatis (cadangan memuat v1_enabled = 1).

Langkah pemulihan manual:
1. Baca log ini dan v1_loop.log/errors.log untuk penyebabnya.
2. Bila ingin bot berjalan lagi dalam mode bayangan: jalankan ulang alat ini
   dengan --jalankan (fase a-e) atau ikuti RUNBOOK_SAKELAR.md f2-f4.
3. Bila ingin memulihkan cadangan: RUNBOOK_SAKELAR.md h butir 3 — setelah
   dipulihkan WAJIB jalankan ulang fase e lalu verifikasi sebelum bot hidup.
4. Jangan menyalakan sakelar dengan UPDATE manual atau aktifkan_live.
"""


class Berhenti(Exception):
    """Fase gagal — berhenti total, tidak ada fase berikutnya yang dijalankan."""


@dataclass
class Hasil:
    """Bentuk minimal hasil perintah eksternal (mirip CompletedProcess)."""
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class Sesi:
    """Semua dependensi + keadaan satu kali jalan (disuntik pemanggil)."""
    pelaksana: Callable
    jam: Callable
    tidur: Callable
    konfirmasi: Callable
    cetak: Callable
    periksa_admin: Callable
    repo_dir: str
    db_path: str
    cadangan_dir: str
    tunggu_menit: int
    jalankan: bool
    ya: bool
    log: "_Log"
    langkah: list = field(default_factory=list)

    def tulis(self, pesan=""):
        """Cetak ke layar dan ke log (log tidak pernah memuat rahasia)."""
        self.cetak(pesan)
        self.log.tulis(pesan)


class _Log:
    """Log baris-per-baris ke `logs/matikan_sakelar_<stempel>.log`."""

    def __init__(self, jalur):
        self.jalur = jalur
        os.makedirs(os.path.dirname(jalur), exist_ok=True)
        with open(jalur, "w", encoding="utf-8"):
            pass

    def tulis(self, pesan):
        with open(self.jalur, "a", encoding="utf-8") as f:
            f.write(str(pesan) + "\n")


def _pelaksana_asli(perintah, *, cwd=None):
    """Jalankan satu perintah tanpa shell; keluaran ditangkap sebagai teks."""
    hasil = subprocess.run(
        perintah, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return Hasil(hasil.returncode, hasil.stdout or "", hasil.stderr or "")


def _periksa_admin_asli():
    """True hanya bila proses ini benar-benar Administrator (ragu = False)."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _konfirmasi_asli(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def _powershell(skrip):
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", skrip]


def _stempel(ts):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))


def _keluaran(s, perintah, label, *, boleh_gagal=False):
    hasil = s.pelaksana(perintah, cwd=s.repo_dir)
    if hasil.returncode != 0 and not boleh_gagal:
        pesan = (hasil.stderr or hasil.stdout or "").strip()
        raise Berhenti(f"{label} gagal (kode {hasil.returncode}): {pesan}")
    return (hasil.stdout or "").strip()


def _parse_proses(teks):
    """Ubah keluaran `ConvertTo-Json` (objek tunggal atau larik) jadi list."""
    teks = (teks or "").strip().lstrip("\ufeff")
    if not teks:
        return []
    data = json.loads(teks)
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def _proses_bot(daftar):
    """Saring proses: wscript run_v1_hidden.vbs atau python main_loop.py.

    Dashboard (streamlit/dashboard.py) tidak pernah cocok dengan penyaring ini,
    jadi proses dashboard tidak pernah masuk daftar untuk dimatikan.
    """
    cocok = []
    for p in daftar:
        nama = str(p.get("Name") or "").lower()
        cmd = str(p.get("CommandLine") or "").lower()
        if nama == "wscript.exe" and "run_v1_hidden" in cmd:
            cocok.append(p)
        elif nama in ("python.exe", "pythonw.exe") and "main_loop.py" in cmd:
            cocok.append(p)
    return cocok


def _ambil_proses(s):
    hasil = s.pelaksana(_powershell(_SKRIP_PROSES), cwd=s.repo_dir)
    if hasil.returncode != 0:
        pesan = (hasil.stderr or hasil.stdout or "").strip()
        raise Berhenti(f"gagal membaca daftar proses (kode {hasil.returncode}): {pesan}")
    return _parse_proses(hasil.stdout)


def _konfirmasi_fase(s, fase):
    if s.ya:
        return
    jawab = s.konfirmasi(f"Fase {fase}. Ketik LANJUT untuk melanjutkan: ")
    if str(jawab).strip().upper() != "LANJUT":
        raise Berhenti(f"konfirmasi fase {fase} tidak diberikan — tidak ada perubahan.")


def _di_dalam_repo(jalur, repo_dir):
    j = os.path.abspath(jalur).lower()
    r = os.path.abspath(repo_dir).lower()
    return j == r or j.startswith(r + os.sep)


def fase_a_prasyarat(s):
    """Periksa prasyarat; tidak mengubah apa pun kecuali mencetak."""
    if not s.periksa_admin():
        raise Berhenti("bukan PowerShell Administrator — hak admin wajib.")
    cabang = _keluaran(
        s, ["git", "-C", s.repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
        "cabang git")
    commit = _keluaran(
        s, ["git", "-C", s.repo_dir, "log", "-1", "--format=%h %s"],
        "commit git")
    kotor = _keluaran(
        s, ["git", "-C", s.repo_dir, "status", "--short"], "status git",
        boleh_gagal=True)
    s.tulis(f"Repo   : {s.repo_dir}")
    s.tulis(f"Cabang : {cabang}")
    s.tulis(f"Commit : {commit}")
    if cabang not in ("main", "fix/review-p1"):
        s.tulis("PERINGATAN: cabang bukan main/fix/review-p1 — pastikan ini "
                "disengaja.")
    if kotor:
        s.tulis("PERINGATAN: working tree kotor:")
        for baris in kotor.splitlines():
            s.tulis("  " + baris)
    if not os.path.isfile(s.db_path):
        raise Berhenti(f"berkas DB tidak ada: {s.db_path}")
    s.tulis(f"DB     : {s.db_path}")
    s.langkah.append("a")


def fase_b_nonaktifkan_tugas(s):
    """Nonaktifkan HANYA IndodaxV1Shadow; IndodaxDashboard tidak disentuh."""
    s.tulis(f"Fase b: nonaktifkan tugas {NAMA_TUGAS} (hanya tugas ini).")
    if not s.jalankan:
        s.tulis(f"  DRY-RUN: Disable-ScheduledTask + Stop-ScheduledTask "
                f"{NAMA_TUGAS}; state diverifikasi Disabled.")
        return
    _konfirmasi_fase(s, "b")
    skrip = (
        f"Disable-ScheduledTask -TaskName '{NAMA_TUGAS}' -ErrorAction Stop | "
        "Out-Null; "
        f"Stop-ScheduledTask -TaskName '{NAMA_TUGAS}' -ErrorAction SilentlyContinue; "
        f"(Get-ScheduledTask -TaskName '{NAMA_TUGAS}').State")
    hasil = s.pelaksana(_powershell(skrip), cwd=s.repo_dir)
    if hasil.returncode != 0 or "Disabled" not in (hasil.stdout or ""):
        pesan = (hasil.stderr or hasil.stdout or "").strip()
        raise Berhenti(f"tugas tidak berstatus Disabled: {pesan!r}")
    s.tulis(f"  {NAMA_TUGAS}: Disabled")
    s.langkah.append("b")


def fase_c_hentikan_proses(s):
    """Matikan pohon proses bot; verifikasi tidak ada main_loop.py tersisa."""
    s.tulis("Fase c: hentikan pohon proses bot "
            "(wscript run_v1_hidden + python main_loop.py).")
    proses = _proses_bot(_ambil_proses(s))
    if not proses:
        s.tulis("  Tidak ada proses bot yang berjalan.")
        s.langkah.append("c")
        return
    for p in proses:
        s.tulis(f"  PID {p.get('ProcessId')} ({p.get('Name')}): "
                f"{p.get('CommandLine')}")
    if not s.jalankan:
        s.tulis("  DRY-RUN: proses di atas akan dimatikan dengan taskkill /F /T.")
        return
    _konfirmasi_fase(s, "c")
    for p in proses:
        pid = str(p.get("ProcessId"))
        hasil = s.pelaksana(["taskkill", "/F", "/T", "/PID", pid], cwd=s.repo_dir)
        s.tulis(f"  taskkill /F /T /PID {pid} -> kode {hasil.returncode}")
    sisa = []
    for _ in range(3):
        s.tidur(2)
        sisa = _proses_bot(_ambil_proses(s))
        if not sisa:
            s.tulis("  Verifikasi: tidak ada proses bot tersisa.")
            s.langkah.append("c")
            return
    pid = ", ".join(str(p.get("ProcessId")) for p in sisa)
    raise Berhenti(f"proses bot masih hidup setelah taskkill: PID {pid}")


def cadangkan_db(db_path, tujuan):
    """Salin DB lewat API backup sqlite3, lalu verifikasi salinannya.

    Mengembalikan `(ok, pesan)`. `src.backup(dst)` dipakai, bukan salin berkas
    mentah: WAL ikut terbawa dan salinan tidak bisa setengah jadi.
    """
    if os.path.exists(tujuan):
        return False, f"tujuan sudah ada, tidak ditimpa: {tujuan}"
    os.makedirs(os.path.dirname(tujuan), exist_ok=True)
    src = dst = None
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tujuan)
        src.backup(dst)
        dst.commit()
        integritas = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integritas != "ok":
            return False, f"integrity_check salinan: {integritas!r}"
        for tabel in ("bot_state", "trades", "proposals"):
            asli = src.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
            salinan = dst.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
            if asli != salinan:
                return False, f"jumlah baris {tabel}: {asli} != {salinan}"
    except sqlite3.Error as e:
        return False, f"sqlite: {e}"
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
    return True, "ok"


def fase_d_cadangkan(s):
    """Cadangkan DB ke luar repo; gagal = berhenti sebelum sakelar disentuh."""
    tujuan = os.path.join(
        s.cadangan_dir, f"portfolio_{_stempel(s.jam())}.db")
    s.tulis(f"Fase d: cadangkan DB ke {tujuan}")
    if _di_dalam_repo(tujuan, s.repo_dir):
        raise Berhenti("folder cadangan berada DI DALAM repo — pilih folder lain")
    if not s.jalankan:
        s.tulis("  DRY-RUN: cadangan tidak dibuat.")
        return
    _konfirmasi_fase(s, "d")
    ok, pesan = cadangkan_db(s.db_path, tujuan)
    if not ok:
        raise Berhenti(f"cadangan gagal diverifikasi: {pesan}")
    s.tulis("  Cadangan OK: integrity_check ok, jumlah baris bot_state/trades/"
            "proposals sama.")
    s.langkah.append("d")


def matikan_sakelar(db_path, *, now):
    """Matikan sakelar lewat SATU jalur resmi, lalu verifikasi hasilnya.

    `main_loop.set_shadow_day(conn, 0)` adalah jalur RUNBOOK §e1: ia memanggil
    `ensure_bot_state`, menulis `v1_enabled = 0` (karena 0 < 7), dan mencatat
    `bot_state_audit` dalam transaksi yang sama. Alat ini tidak pernah menulis
    `v1_enabled = 1` dan tidak memanggil `aktifkan_live`.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        main_loop.set_shadow_day(conn, 0, now=now)
        baris = conn.execute(
            "SELECT v1_enabled, shadow_day FROM bot_state WHERE id = 1"
        ).fetchone()
        if int(baris["v1_enabled"]) != 0:
            return False, f"v1_enabled masih {baris['v1_enabled']}"
        if int(baris["shadow_day"]) != 0:
            return False, f"shadow_day masih {baris['shadow_day']}"
        audit = conn.execute(
            "SELECT COUNT(*) FROM bot_state_audit "
            "WHERE sumber = 'set_shadow_day' AND ts = ?", (now,)).fetchone()[0]
        if audit < 1:
            return False, "baris bot_state_audit tidak ditemukan"
    finally:
        conn.close()
    return True, "ok"


def fase_e_matikan_sakelar(s):
    s.tulis("Fase e: matikan sakelar lewat main_loop.set_shadow_day(conn, 0).")
    if not s.jalankan:
        s.tulis("  DRY-RUN: v1_enabled 1 -> 0, shadow_day -> 0, audit dicatat.")
        return
    _konfirmasi_fase(s, "e")
    ok, pesan = matikan_sakelar(s.db_path, now=s.jam())
    if not ok:
        raise Berhenti(f"verifikasi sakelar gagal: {pesan}")
    s.tulis("  v1_enabled = 0, shadow_day = 0, baris bot_state_audit tercatat.")
    s.langkah.append("e")


def fase_f_nyalakan(s):
    """Aktifkan tugas, jalankan, lalu pantau preflight sampai GO/waktu habis."""
    s.tulis(f"Fase f: aktifkan lagi {NAMA_TUGAS}, jalankan, lalu pantau "
            f"preflight tiap {JEDA_PREFLIGHT_S} detik selama "
            f"{s.tunggu_menit} menit.")
    if not s.jalankan:
        s.tulis("  DRY-RUN: Enable-ScheduledTask + schtasks /Run, lalu "
                "python -m preflight --target bayangan berulang.")
        return True
    _konfirmasi_fase(s, "f")
    skrip = (
        f"Enable-ScheduledTask -TaskName '{NAMA_TUGAS}' -ErrorAction Stop | "
        "Out-Null; "
        f"(Get-ScheduledTask -TaskName '{NAMA_TUGAS}').State")
    hasil = s.pelaksana(_powershell(skrip), cwd=s.repo_dir)
    if hasil.returncode != 0 or not any(
            x in (hasil.stdout or "") for x in ("Ready", "Running")):
        pesan = (hasil.stderr or hasil.stdout or "").strip()
        raise Berhenti(f"tugas tidak bisa diaktifkan lagi: {pesan!r}")
    s.tulis(f"  {NAMA_TUGAS}: {(hasil.stdout or '').strip()}")
    hasil = s.pelaksana(
        ["schtasks", "/Run", "/TN", NAMA_TUGAS], cwd=s.repo_dir)
    if hasil.returncode != 0:
        pesan = (hasil.stderr or hasil.stdout or "").strip()
        raise Berhenti(f"schtasks /Run gagal (kode {hasil.returncode}): {pesan}")

    percobaan = max(1, s.tunggu_menit * 60 // JEDA_PREFLIGHT_S + 1)
    terakhir = ""
    for i in range(percobaan):
        hasil = s.pelaksana(
            [sys.executable, "-m", "preflight", "--target", "bayangan"],
            cwd=s.repo_dir)
        terakhir = (hasil.stdout or "").strip()
        if hasil.returncode == 0:
            s.tulis(terakhir)
            s.tulis("GO — preflight lolos. Bot berjalan dalam mode bayangan.")
            s.langkah.append("f")
            return True
        s.tulis(f"  Percobaan {i + 1}/{percobaan}: belum GO "
                f"(kode {hasil.returncode}).")
        if i < percobaan - 1:
            s.tidur(JEDA_PREFLIGHT_S)
    s.tulis(terakhir)
    s.tulis("NO-GO — waktu tunggu habis. Bot TETAP berjalan (sakelar sudah "
            "mati); baca baris GAGAL/TAK TERBACA di atas.")
    return False


def _argumen(argv=None):
    parser = argparse.ArgumentParser(
        description="Matikan sakelar eksekusi bot (otomasi RUNBOOK_SAKELAR.md). "
                    "Tanpa --jalankan hanya DRY-RUN.")
    parser.add_argument("--jalankan", action="store_true",
                        help="benar-benar mengubah tugas/proses/DB")
    parser.add_argument("--ya", action="store_true",
                        help="lewati konfirmasi LANJUT di tiap fase")
    parser.add_argument("--db", default=None,
                        help="jalur portfolio.db (bawaan: di root repo)")
    parser.add_argument("--cadangan-dir", default=None,
                        help="folder cadangan DI LUAR repo "
                             "(bawaan: %%USERPROFILE%%\\Documents\\bot-backup)")
    parser.add_argument("--tunggu-menit", type=int, default=TUNGGU_MENIT_BAWAAN,
                        help="batas tunggu preflight GO (bawaan 20)")
    return parser.parse_args(argv)


def main(argv=None, *, pelaksana=_pelaksana_asli, jam=time.time,
         tidur=time.sleep, konfirmasi=_konfirmasi_asli, cetak=print,
         periksa_admin=_periksa_admin_asli, repo_dir=None):
    args = _argumen(argv)
    repo_dir = os.path.abspath(repo_dir or _REPO)
    db_path = os.path.abspath(args.db or os.path.join(repo_dir, "portfolio.db"))
    cadangan_dir = os.path.abspath(
        args.cadangan_dir
        or os.path.join(os.path.expanduser("~"), "Documents", "bot-backup"))
    log = _Log(os.path.join(repo_dir, "logs",
                            f"matikan_sakelar_{_stempel(jam())}.log"))
    sesi = Sesi(
        pelaksana=pelaksana, jam=jam, tidur=tidur, konfirmasi=konfirmasi,
        cetak=cetak, periksa_admin=periksa_admin, repo_dir=repo_dir,
        db_path=db_path, cadangan_dir=cadangan_dir,
        tunggu_menit=max(0, args.tunggu_menit), jalankan=args.jalankan,
        ya=args.ya, log=log)

    sesi.tulis("matikan_sakelar — " +
               ("JALAN (mengubah keadaan)" if sesi.jalankan
                else "DRY-RUN (tidak mengubah apa pun)"))
    try:
        fase_a_prasyarat(sesi)
        fase_b_nonaktifkan_tugas(sesi)
        fase_c_hentikan_proses(sesi)
        fase_d_cadangkan(sesi)
        fase_e_matikan_sakelar(sesi)
        go = fase_f_nyalakan(sesi)
    except Berhenti as e:
        sesi.tulis(f"BERHENTI: {e}")
        sesi.tulis("")
        sesi.tulis("Fase yang sudah dijalankan: " +
                   (", ".join(sesi.langkah) or "belum ada"))
        sesi.tulis(_PEMULIHAN)
        return 1
    sesi.tulis(f"Selesai. Log: {sesi.log.jalur}")
    return 0 if go else 1


if __name__ == "__main__":
    raise SystemExit(main())
