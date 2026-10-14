import growth_report as g


def test_splits_contributions_from_market():
    snaps = [{"ts": 1000, "total_idr": 700000},
             {"ts": 2000, "total_idr": 800000}]
    deps = [{"idr": 500000, "ts": 1500}]
    rows = g.monthly_report(snaps, deps)
    assert rows[-1]["contributions_idr"] == 500000
    assert rows[-1]["market_idr"] == 800000 - 700000 - 500000
    assert rows[-1]["end_value_idr"] == 800000
