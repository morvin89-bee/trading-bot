import portfolio as p

V2 = {"balances": [
    {"asset": "BTC", "free": "0.0005", "locked": "0"},
    {"asset": "IDR", "free": "203394", "locked": "0"},
    {"asset": "SOL", "free": "0", "locked": "0"},
]}
V1 = {"success": 1, "return": {"balance": {"btc": "0.0005", "bch": "0.000583"},
                               "balance_rp": "203394"}}


def test_parse_v2_merges_free_locked_and_filters_zero():
    b = p.parse_balances(V2)
    assert b["btc"] == 0.0005
    assert b["IDR"] == 203394
    assert "sol" not in b          # nol difilter


def test_parse_v1_shape():
    b = p.parse_balances(V1)
    assert b["btc"] == 0.0005
    assert b["bch"] == 0.000583
    assert b["IDR"] == 203394
