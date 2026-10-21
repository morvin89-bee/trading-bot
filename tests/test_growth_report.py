import growth_report as g


def _ts(tanggal):
    import time
    return int(time.mktime(time.strptime(tanggal, "%Y-%m-%d")))


def test_splits_contributions_from_market():
    snaps = [{"ts": 1000, "total_idr": 700000},
             {"ts": 2000, "total_idr": 800000}]
    deps = [{"idr": 500000, "ts": 1500}]
    rows = g.monthly_report(snaps, deps)
    assert rows[-1]["contributions_idr"] == 500000
    assert rows[-1]["market_idr"] == 800000 - 700000 - 500000
    assert rows[-1]["end_value_idr"] == 800000


def test_opening_is_zero_when_no_snapshot_before_first_deposit():
    # Setoran 1 Okt, snapshot pertama 14 Okt -> snapshot pertama bulan itu
    # SUDAH memuat setoran itu, jadi tak boleh dipakai sebagai "awal".
    snaps = [{"ts": _ts("2026-10-14"), "total_idr": 503135.34}]
    deps = [{"idr": 502606.0, "ts": _ts("2026-10-01")}]
    r = g.monthly_report(snaps, deps)[-1]
    assert r["opening_value_idr"] == 0
    assert r["opening_known"] is False
    assert r["market_idr"] == 503135.34 - 502606.0


def test_opening_is_last_snapshot_before_first_deposit():
    snaps = [{"ts": _ts("2026-10-01"), "total_idr": 100000.0},
             {"ts": _ts("2026-10-20"), "total_idr": 650000.0}]
    deps = [{"idr": 500000.0, "ts": _ts("2026-10-15")}]
    r = g.monthly_report(snaps, deps)[-1]
    assert r["opening_value_idr"] == 100000.0
    assert r["opening_known"] is True
    assert r["market_idr"] == 650000.0 - 100000.0 - 500000.0


def test_month_without_deposit_carries_value_from_previous_month():
    snaps = [{"ts": _ts("2026-09-20"), "total_idr": 100000.0},
             {"ts": _ts("2026-10-20"), "total_idr": 200000.0}]
    rows = {r["month"]: r for r in g.monthly_report(snaps, [])}
    okt = rows["2026-10"]
    assert okt["opening_value_idr"] == 100000.0
    assert okt["opening_known"] is True
    assert okt["market_idr"] == 100000.0


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
