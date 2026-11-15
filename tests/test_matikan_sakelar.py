"""Tes tools/matikan_sakelar.py — tanpa Windows nyata, tanpa DB asli.

Semua efek eksternal (subprocess, jam, tidur, konfirmasi, cek admin) disuntikkan.
DB yang dipakai selalu SQLite sementara di `tmp_path`.
"""
import ast
import inspect
import json
import sqlite3

import pytest

import dca_window
from tools import matikan_sakelar as ms


class Jam:
    """Jam palsu yang hanya maju lewat `Tidur`."""

    def __init__(self, awal=1_700_000_000.0):
        self.kini = awal

    def __call__(self):
        return self.kini

    def maju(self, detik):
        self.kini += detik


class Tidur:
    def __init__(self, jam):
        self.jam = jam
        self.panggilan = []

    def __call__(self, detik):
        self.panggilan.append(detik)
        self.jam.maju(detik)


class Pelaksana:
    """Pelaksana palsu: mencatat perintah, menjawab sesuai jenisnya."""

    def __init__(self, proses=None, preflight=(0, "GO — semua pemeriksaan lolos")):
        self.panggilan = []
        self.proses = list(proses or [])
        self.preflight = preflight
        self.ampuh = True          # False = taskkill tidak mempan
        self.cabang = "fix/review-p1"
        self.kotor = ""
        self.state_disable = "Disabled"
        self.state_enable = "Ready"

    def __call__(self, perintah, *, cwd=None):
        self.panggilan.append(list(perintah))
        teks = " ".join(str(p) for p in perintah).lower()
        if "get-ciminstance" in teks:
            return ms.Hasil(0, json.dumps(self.proses) if self.proses else "")
        if perintah[0] == "taskkill":
            if self.ampuh:
                pid = int(perintah[-1])
                self.proses = [p for p in self.proses
                               if int(p["ProcessId"]) != pid]
            return ms.Hasil(0, "")
        if "rev-parse" in teks:
            return ms.Hasil(0, self.cabang + "\n")
        if "status" in perintah and "--short" in perintah:
            return ms.Hasil(0, self.kotor)
        if "log" in perintah and "-1" in perintah:
            return ms.Hasil(0, "abc1234 pesan\n")
        if "disable-scheduledtask" in teks:
            return ms.Hasil(0, self.state_disable + "\n")
        if "enable-scheduledtask" in teks:
            return ms.Hasil(0, self.state_enable + "\n")
        if "preflight" in teks:
            return ms.Hasil(self.preflight[0], self.preflight[1])
        return ms.Hasil(0, "")

    def mutasi(self):
        """Perintah yang mengubah keadaan (bukan sekadar membaca)."""
        kunci = ("taskkill", "disable-scheduledtask", "enable-scheduledtask",
                 "schtasks")
        return [p for p in self.panggilan
                if any(k in " ".join(p).lower() for k in kunci)]


def _proses_bot(pid=111, nama="pythonw.exe",
                cmd="pythonw.exe C:\\bot\\main_loop.py"):
    return {"ProcessId": pid, "ParentProcessId": 5, "Name": nama,
            "CommandLine": cmd}


def _proses_wscript(pid=100):
    return {"ProcessId": pid, "ParentProcessId": 5, "Name": "wscript.exe",
            "CommandLine": "wscript.exe C:\\bot\\run_v1_hidden.vbs"}


@pytest.fixture
def db(tmp_path):
    """DB sementara yang menyerupai produksi: sakelar nyala, 5 hari bayangan."""
    conn = sqlite3.connect(tmp_path / "portfolio.db")
    dca_window.ensure_bot_state(conn)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY)")
    conn.execute(
        "UPDATE bot_state SET v1_enabled = 1, shadow_day = 5, "
        "last_dca_date = '2026-11', dca_status = 'awaiting' WHERE id = 1")
    conn.commit()
    conn.close()


def _sakelar(tmp_path):
    conn = sqlite3.connect(tmp_path / "portfolio.db")
    try:
        return tuple(conn.execute(
            "SELECT v1_enabled, shadow_day FROM bot_state WHERE id = 1"
        ).fetchone())
    finally:
        conn.close()


def _cadangan_dir(tmp_path):
    return tmp_path.parent / ("cadangan-" + tmp_path.name)


def _jalan(tmp_path, pelaksana, jam, *argtambahan, jawab="LANJUT", admin=True,
           jalankan=True):
    keluaran = []
    tidur = Tidur(jam)
    argumen = ["--cadangan-dir", str(_cadangan_dir(tmp_path)), *argtambahan]
    if jalankan:
        argumen.insert(0, "--jalankan")
    kode = ms.main(
        argumen, pelaksana=pelaksana, jam=jam, tidur=tidur,
        konfirmasi=lambda prompt: jawab, cetak=keluaran.append,
        periksa_admin=lambda: admin, repo_dir=str(tmp_path))
    return kode, keluaran, tidur


# ---------------------------------------------------------------------------
# Dry-run & prasyarat
# ---------------------------------------------------------------------------

def test_dry_run_tidak_mengubah_apa_pun(db, tmp_path):
    """Tanpa --jalankan: prasyarat diperiksa, tidak ada mutasi apa pun."""
    pelaksana = Pelaksana(proses=[_proses_bot()])

    kode, keluaran, _ = _jalan(
        tmp_path, pelaksana, Jam(), jalankan=False)

    assert kode == 0, keluaran
    assert _sakelar(tmp_path) == (1, 5)
    assert pelaksana.mutasi() == [], pelaksana.panggilan
    assert not _cadangan_dir(tmp_path).exists()
    assert any("DRY-RUN" in b for b in keluaran)
    assert any("111" in b for b in keluaran), "daftar proses harus tampil"


def test_bukan_admin_berhenti_tanpa_perubahan(db, tmp_path):
    pelaksana = Pelaksana()

    kode, keluaran, _ = _jalan(tmp_path, pelaksana, Jam(), admin=False)

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5)
    assert pelaksana.mutasi() == []
    assert any("Administrator" in b for b in keluaran)


def test_konfirmasi_ditolak_tidak_mengubah_apa_pun(db, tmp_path):
    """Satu fase menolak = berhenti total sebelum mutasi berikutnya."""
    pelaksana = Pelaksana()

    kode, keluaran, _ = _jalan(
        tmp_path, pelaksana, Jam(), jawab="tidak")

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5)
    assert pelaksana.mutasi() == []
    assert any("konfirmasi" in b for b in keluaran)


# ---------------------------------------------------------------------------
# Fase b & c
# ---------------------------------------------------------------------------

def test_tugas_dashboard_tidak_disentuh(db, tmp_path):
    """Hanya IndodaxV1Shadow yang boleh disebut; dashboard tidak pernah."""
    pelaksana = Pelaksana(proses=[_proses_bot()])

    kode, _, _ = _jalan(tmp_path, pelaksana, Jam())

    assert kode == 0
    semua = " ".join(" ".join(p) for p in pelaksana.panggilan).lower()
    assert "indodaxdashboard" not in semua
    assert not any("dashboard" in " ".join(p).lower()
                   for p in pelaksana.mutasi()), pelaksana.mutasi()
    assert any("indodaxv1shadow" in " ".join(p).lower()
               for p in pelaksana.mutasi())


def test_proses_masih_ada_berhenti(db, tmp_path):
    """Verifikasi ulang: proses yang tidak mati = BERHENTI, sakelar utuh."""
    pelaksana = Pelaksana(proses=[_proses_bot()])
    pelaksana.ampuh = False

    kode, keluaran, tidur = _jalan(tmp_path, pelaksana, Jam())

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5), "fase e tidak boleh jalan"
    assert any("masih hidup" in b for b in keluaran), keluaran
    assert tidur.panggilan == [2, 2, 2]
    assert not any("enable-scheduledtask" in " ".join(p).lower()
                   for p in pelaksana.panggilan)


def test_proses_dashboard_tidak_masuk_daftar():
    daftar = [
        _proses_bot(),
        _proses_wscript(),
        {"ProcessId": 7, "Name": "python.exe",
         "CommandLine": "python -m streamlit run dashboard/app.py"},
    ]

    hasil = ms._proses_bot(daftar)

    assert [p["ProcessId"] for p in hasil] == [111, 100]


# ---------------------------------------------------------------------------
# Fase d — cadangan
# ---------------------------------------------------------------------------

def test_cadangan_korup_berhenti_sebelum_fase_e(db, tmp_path, monkeypatch):
    """Cadangan gagal diverifikasi = berhenti sebelum sakelar disentuh."""
    monkeypatch.setattr(ms, "cadangkan_db",
                        lambda db_path, tujuan: (False, "integrity_check: rusak"))
    pelaksana = Pelaksana(proses=[_proses_bot()])

    kode, keluaran, _ = _jalan(tmp_path, pelaksana, Jam())

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5)
    assert any("integrity_check" in b for b in keluaran), keluaran
    assert not any("enable-scheduledtask" in " ".join(p).lower()
                   for p in pelaksana.panggilan)


def test_cadangan_di_dalam_repo_ditolak(db, tmp_path):
    pelaksana = Pelaksana()

    kode, keluaran, _ = _jalan(
        tmp_path, pelaksana, Jam(),
        "--cadangan-dir", str(tmp_path / "cadangan"))

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5)
    assert any("DI DALAM repo" in b for b in keluaran), keluaran


def test_cadangan_asli_menyalin_dan_menghitung(db, tmp_path):
    tujuan = _cadangan_dir(tmp_path) / "salinan.db"

    ok, pesan = ms.cadangkan_db(str(tmp_path / "portfolio.db"), str(tujuan))

    assert ok is True, pesan
    conn = sqlite3.connect(tujuan)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT v1_enabled FROM bot_state WHERE id = 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_cadangan_menolak_sumber_rusak(tmp_path):
    sumber = tmp_path / "rusak.db"
    sumber.write_bytes(b"ini bukan basis data sqlite")

    ok, pesan = ms.cadangkan_db(str(sumber), str(tmp_path / "salinan.db"))

    assert ok is False
    assert "sqlite" in pesan.lower(), pesan


# ---------------------------------------------------------------------------
# Fase e & f
# ---------------------------------------------------------------------------

def test_sakelar_1_ke_0_dan_audit_tercatat(db, tmp_path):
    """Jalur resmi: v1_enabled 1->0, shadow_day 0, audit, jendela DCA utuh."""
    pelaksana = Pelaksana(proses=[_proses_wscript(), _proses_bot()])

    kode, keluaran, _ = _jalan(tmp_path, pelaksana, Jam())

    assert kode == 0, keluaran
    assert _sakelar(tmp_path) == (0, 0)
    conn = sqlite3.connect(tmp_path / "portfolio.db")
    try:
        audit = conn.execute(
            "SELECT COUNT(*) FROM bot_state_audit "
            "WHERE sumber = 'set_shadow_day'").fetchone()[0]
        jendela = conn.execute(
            "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert audit >= 1
    assert tuple(jendela) == ("2026-11", "awaiting"), "jendela DCA tidak disentuh"
    assert any("GO —" in b for b in keluaran), keluaran
    assert any("schtasks" in " ".join(p).lower()
               for p in pelaksana.panggilan)


def test_gagal_verifikasi_bot_tetap_mati(db, tmp_path, monkeypatch):
    """Verifikasi gagal = tidak ada re-enable; bot dibiarkan mati."""
    monkeypatch.setattr(ms.main_loop, "set_shadow_day",
                        lambda conn, day, now=None: None)
    pelaksana = Pelaksana(proses=[_proses_bot()])

    kode, keluaran, _ = _jalan(tmp_path, pelaksana, Jam())

    assert kode == 1
    assert _sakelar(tmp_path) == (1, 5)
    assert any("v1_enabled" in b for b in keluaran), keluaran
    assert not any("enable-scheduledtask" in " ".join(p).lower()
                   for p in pelaksana.panggilan)


def test_preflight_no_go_mencetak_alasan_dan_tidak_mematikan_bot(
        db, tmp_path):
    pelaksana = Pelaksana(
        proses=[_proses_bot()],
        preflight=(1, "NO-GO - metadata pair: basi/hilang"))

    kode, keluaran, _ = _jalan(
        tmp_path, pelaksana, Jam(), "--tunggu-menit", "1")

    assert kode == 1
    assert any("NO-GO" in b and "metadata" in b for b in keluaran), keluaran
    # Bot sengaja dibiarkan berjalan: sakelar sudah mati, tugas tetap aktif.
    assert _sakelar(tmp_path) == (0, 0)


# ---------------------------------------------------------------------------
# Invarian sumber & parsing
# ---------------------------------------------------------------------------

def test_tidak_ada_jalur_menulis_v1_enabled_1():
    """AST, bukan teks: docstring/komentar boleh menyebut, kode tidak boleh."""
    pohon = ast.parse(inspect.getsource(ms))
    for node in ast.walk(pohon):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            teks = node.value.upper()
            assert "SET V1_ENABLED" not in teks
            assert "UPDATE BOT_STATE" not in teks
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            nama = {t.id for t in ast.walk(node) if isinstance(t, ast.Name)}
            assert "v1_enabled" not in nama, "tidak boleh menulis v1_enabled"
        elif isinstance(node, ast.Call):
            fungsi = node.func
            nama = getattr(fungsi, "attr", None) or getattr(fungsi, "id", None)
            assert nama != "aktifkan_live", "aktifkan_live tidak boleh dipanggil"
    assert "set_shadow_day(conn, 0" in inspect.getsource(ms)


def test_parse_proses_objek_tunggal_larik_dan_kosong():
    satu = {"ProcessId": 1, "Name": "pythonw.exe", "CommandLine": "x"}

    assert ms._parse_proses("") == []
    assert ms._parse_proses(json.dumps(satu)) == [satu]
    assert ms._parse_proses(json.dumps([satu])) == [satu]
