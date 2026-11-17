"""`RESERVE_IDR` — satu angka cadangan untuk bot dan alert harian."""
import pytest

import portfolio


def test_tanpa_kunci_berarti_nol():
    # Perilaku lama: tanpa angka, reserve buy tidak pernah diusulkan.
    assert portfolio.reserve_idr(env={}) == 0.0


def test_titik_ribuan_dan_prefix_rp_diterima():
    assert portfolio.reserve_idr(env={"RESERVE_IDR": "Rp 25.000"}) == 25000.0


def test_bukan_angka_mengangkat_bukan_diam_diam_nol():
    # 0.0 diam-diam akan membuat alert memakai seluruh uang tunai lagi.
    with pytest.raises(ValueError):
        portfolio.reserve_idr(env={"RESERVE_IDR": "dua puluh ribu"})


def test_negatif_mengangkat():
    with pytest.raises(ValueError):
        portfolio.reserve_idr(env={"RESERVE_IDR": "-5000"})


def test_environment_menang_atas_file(monkeypatch):
    monkeypatch.setenv("RESERVE_IDR", "30000")
    assert portfolio.reserve_idr(env={"RESERVE_IDR": "25000"}) == 30000.0
