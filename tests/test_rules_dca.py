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
