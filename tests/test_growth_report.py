import growth_report as g


def test_splits_contributions_from_market():
    snaps = [{"ts": 1000, "total_idr": 700000},
             {"ts": 2000, "total_idr": 800000}]
    deps = [{"idr": 500000, "ts": 1500}]
    rows = g.monthly_report(snaps, deps)
    assert rows[-1]["contributions_idr"] == 500000
    assert rows[-1]["market_idr"] == 800000 - 700000 - 500000
    assert rows[-1]["end_value_idr"] == 800000


def test_month_bucketing_matches_local_deposit_dates():
    import portfolio_cli as cli
    ts = cli._parse_date("2026-10-01")     # setoran memakai waktu lokal
    assert g._month(ts) == "2026-10"       # jadi bulan pun harus lokal


def test_month_contributions_sums_only_that_month():
    import time
    t_oct = int(time.mktime(time.strptime("2026-10-05", "%Y-%m-%d")))
    t_nov = int(time.mktime(time.strptime("2026-11-05", "%Y-%m-%d")))
    deps = [{"idr": 500000, "ts": t_oct}, {"idr": 300000, "ts": t_nov}]
    assert g.month_contributions(deps, t_oct) == 500000
