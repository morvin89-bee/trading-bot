import sqlite3

import migrate_dca as m
import portfolio_store as ps


def _make_dca(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE purchases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " pair TEXT, idr REAL, price REAL, coins REAL,"
              " bought_at INTEGER, api_trade_id TEXT UNIQUE)")
    c.execute("INSERT INTO purchases (pair,idr,price,coins,bought_at,api_trade_id)"
              " VALUES ('eth_idr',198129,44439000,0.00445846,1791570000,'T1')")
    c.execute("INSERT INTO purchases (pair,idr,price,coins,bought_at,api_trade_id)"
              " VALUES ('btc_idr',100000,1e9,0.0001,1791570001,NULL)")
    c.commit()
    c.close()


def test_migrate_is_idempotent(tmp_path, tmp_conn):
    dca = tmp_path / "dca.db"
    _make_dca(str(dca))
    assert m.migrate(str(dca), tmp_conn) == 2
    assert m.migrate(str(dca), tmp_conn) == 0
    rows = ps.list_trades(tmp_conn)
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"dca_db"}
