"""Tes tab Shadow — fungsi murni (tanpa jaringan, tanpa render Streamlit)."""
import dashboard.shadow_tab as tab


def _pasang_perekam_st(monkeypatch):
    """Rekam panggilan `st.*` — jalur render diuji tanpa runtime Streamlit."""
    panggilan = {"info": [], "warning": [], "success": [], "error": [],
                 "subheader": []}
    for nama in panggilan:
        monkeypatch.setattr(
            tab.st, nama,
            lambda teks="", _n=nama, **k: panggilan[_n].append(teks))
    return panggilan


def test_sisa_hari_menuju_g5():
    assert tab.sisa_hari(1) == 6
    assert tab.sisa_hari(7) == 0
    # Lewat target pun tidak boleh negatif — angka negatif membingungkan.
    assert tab.sisa_hari(9) == 0


def test_status_sakelar_bahasa_manusia():
    assert "aman" in tab.status_sakelar(0)
    assert "MENYALA" in tab.status_sakelar(1)
    assert "aman" not in tab.status_sakelar(1)


def test_bahasa_hasil_menutup_kode_bursa():
    # Kata kunci pemantauan harian harus tetap dikenali pemindaian teks.
    assert "ditolak" in tab.bahasa_hasil("gate_rejected")
    assert "ditolak" in tab.bahasa_hasil("spread_rejected")
    # Kode asing tidak boleh tampil mentah.
    assert tab.bahasa_hasil("proposed") == "usulan dikirim"
    assert tab.bahasa_hasil("dca_missed") == "DCA terlewat"
    # Kode tak dikenal tampil apa adanya, bukan hilang diam-diam.
    assert tab.bahasa_hasil("aneh") == "aneh"


def test_rekap_hasil_menghitung_per_jenis():
    baris = [("proposed", 2), ("gate_rejected", 1), ("proposed", 1)]
    rekap = tab.rekap_hasil(baris)
    assert rekap["proposed"] == 3
    assert rekap["gate_rejected"] == 1


def test_teks_rekap_jujur_dan_terurut():
    # "Nol penolakan" harus tampil eksplisit — itu kabar baik yang dicari G5.
    assert "nol penolakan" in tab.teks_rekap({})
    teks = tab.teks_rekap({"proposed": 3, "gate_rejected": 2})
    # Jenis terbanyak duluan.
    assert teks.index("usulan dikirim") < teks.index("ditolak")
    assert "3 usulan dikirim" in teks
    assert "2 ditolak" in teks


# --------------------------------------------------------------------------
# Review 2026-10-18 — M1/M5: tab G5 tidak boleh menampilkan angka karangan.
# --------------------------------------------------------------------------

def test_basis_data_kosong_hanya_untuk_skema_yang_belum_ada():
    """M1: hanya "tabel/kolom belum pernah dibuat" = kosong (bukan galat).

    Galat lain — "database is locked", korupsi, dsb. — BUKAN kabar baik:
    memakainya sebagai "belum ada catatan" menampilkan "nol penolakan"
    palsu tepat di tab yang dipercaya untuk G5.
    """
    assert tab.basis_data_kosong("no such table: proposals")
    assert tab.basis_data_kosong("no such column: day")
    assert not tab.basis_data_kosong("database is locked")
    assert not tab.basis_data_kosong("database disk image is malformed")


def test_peringatan_ditolak_memakai_kode_asli():
    """M5: penentu peringatan memakai KODE asli (`*_rejected`), bukan
    substring terjemahan — hasil asing tak dikenal lolos terjemahan tapi
    tetap penolakan dan harus tampil kuning, bukan hijau."""
    assert tab.peringatan_ditolak({"gate_rejected": 1})
    assert tab.peringatan_ditolak({"spread_rejected": 2})
    # Kode asing berakhiran _rejected: gagal diterjemahkan, tetap penolakan.
    assert tab.peringatan_ditolak({"blok_rejected": 1})
    assert not tab.peringatan_ditolak({"proposed": 3})
    assert not tab.peringatan_ditolak({})


def test_galat_basis_data_tidak_diklaim_nol_penolakan(monkeypatch):
    """M1 — jalur render: kunci terkunci ≠ "nol penolakan"."""
    import sqlite3

    panggilan = _pasang_perekam_st(monkeypatch)

    class _ConnKunci:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    tab._rekap_hari_ini(_ConnKunci())

    assert not any("nol penolakan" in p for p in panggilan["info"]), (
        "galat DB tidak boleh menampilkan kabar baik palsu")
    assert any("Gagal membaca basis data" in p for p in panggilan["error"])


def test_tabel_kosong_tetap_info_nol_penolakan(monkeypatch):
    """Pin: tabel yang belum pernah dibuat = memang nol usulan, info tetap."""
    import sqlite3

    panggilan = _pasang_perekam_st(monkeypatch)

    class _ConnKosong:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: proposals")

    tab._rekap_hari_ini(_ConnKosong())

    assert any("nol penolakan" in p for p in panggilan["info"])
    assert not panggilan["error"]


def test_rekap_kode_asing_berakhiran_rejected_jadi_peringatan(monkeypatch):
    """M5 — jalur render: `blok_rejected` tak dikenal = KUNING, bukan hijau."""
    panggilan = _pasang_perekam_st(monkeypatch)

    class _ConnIsi:
        def execute(self, *a, **k):
            class _Hasil:
                def fetchall(self):
                    return [("blok_rejected", 2), ("proposed", 1)]
            return _Hasil()

    tab._rekap_hari_ini(_ConnIsi())

    assert panggilan["warning"], "penolakan asing harus memicu peringatan"
    assert not panggilan["success"], "penolakan tidak boleh tampil hijau"
