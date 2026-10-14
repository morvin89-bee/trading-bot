import portfolio_cli as cli
import portfolio_store as ps


def test_cmd_buy_records_trade(tmp_conn):
    assert cli.cmd_buy(tmp_conn, "sol", 50000, 1_900_000, 1791570000) is True
    t = ps.list_trades(tmp_conn)[0]
    assert t["pair"] == "sol_idr" and t["source"] == "manual"


def test_cmd_deposit_records(tmp_conn):
    cli.cmd_deposit(tmp_conn, 500000, 1791570000, "setoran Okt")
    assert ps.total_contributions(tmp_conn) == 500000
