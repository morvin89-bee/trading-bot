import rules


def test_split_basic_weights():
    out = rules.dca_split(500000, {})
    assert out["btc_idr"] == 275000
    assert out["eth_idr"] == 125000
    assert out["sol_idr"] == 50000
    assert out["IDR"] == 50000
    assert sum(out.values()) == 500000


def test_tilts_toward_most_underweight():
    hv = {"btc_idr": 250000, "eth_idr": 0, "sol_idr": 0, "IDR": 0}
    out = rules.dca_split(500000, hv)
    assert out["eth_idr"] > 125000
    assert sum(out.values()) == 500000


def test_below_min_order_goes_to_idr():
    # 15.000 -> tiap aset < minimum order 10.000, jadi semuanya ke cadangan.
    out = rules.dca_split(15000, {})
    assert out["IDR"] == 15000


def test_total_preserved_even_for_fractional_amount():
    out = rules.dca_split(500000.75, {})
    assert abs(sum(out.values()) - 500000.75) < 1e-6


def test_dca_actions_tanpa_saldo_cukup_tidak_mengusulkan():
    """Setoran bulanan hanya diusulkan bila saldo bebas menutupinya.

    Tanpa cek ini `dca_split` tetap membagi setoran yang tidak ada dan
    gerbang per order menolak sebagian pair satu per satu — pengguna tidak
    pernah tahu bahwa masalahnya saldo, bukan pasar.
    """
    harga = {"btc_idr": 1600.0, "eth_idr": 1400.0, "sol_idr": 350.0}
    assert rules.dca_actions([], 499999, harga, max_order=275000) == []
    cukup = rules.dca_actions([], 500000, harga, max_order=275000)
    assert cukup, "kontrol: saldo tepat setoran harus menghasilkan aksi"


def test_target_weights_must_sum_to_one():
    """Bobot target yang tidak berjumlah 1.0 bukan sekadar tidak rapi: total
    alokasi tidak lagi sama dengan setoran dan `IDR` menyerap selisihnya
    diam-diam. Tolak dengan keras."""
    import pytest
    with pytest.raises(ValueError, match="bobot"):
        rules.dca_split(500000, {}, target={"btc_idr": 0.6, "IDR": 0.6})
    with pytest.raises(ValueError, match="bobot"):
        rules.dca_split(500000, {}, target={"btc_idr": 0.5, "IDR": 0.4})
