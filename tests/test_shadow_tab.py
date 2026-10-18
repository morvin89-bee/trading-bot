"""Tes tab Shadow — fungsi murni (tanpa jaringan, tanpa render Streamlit)."""
import dashboard.shadow_tab as tab


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
