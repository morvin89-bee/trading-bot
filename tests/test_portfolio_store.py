import portfolio_store as ps


def test_insert_trade_dedupes_by_external_id(tmp_conn):
    t = dict(pair="btc_idr", side="buy", price=1e9, qty=0.001, quote=1e6,
             fee=2000, ts=1, source="api", external_id="X1")
    assert ps.insert_trade(tmp_conn, t) is True
    assert ps.insert_trade(tmp_conn, t) is False
    assert len(ps.list_trades(tmp_conn)) == 1


def test_manual_trades_without_external_id_both_insert(tmp_conn):
    a = dict(pair="sol_idr", side="buy", price=1.9e6, qty=0.026,
             quote=50000, fee=0, ts=1, source="manual", external_id=None)
    assert ps.insert_trade(tmp_conn, a) is True
    assert ps.insert_trade(tmp_conn, dict(a)) is True
    assert len(ps.list_trades(tmp_conn)) == 2


def test_total_contributions_sums_deposits(tmp_conn):
    ps.insert_deposit(tmp_conn, 500000, 1)
    ps.insert_deposit(tmp_conn, 250000, 2)
    assert ps.total_contributions(tmp_conn) == 750000
