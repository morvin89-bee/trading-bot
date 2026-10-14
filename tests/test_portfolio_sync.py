import portfolio as p
import portfolio_store as ps


def test_sync_trades_stores_buys_and_sells_and_dedupes(tmp_conn, monkeypatch):
    fake = [{"id": "7", "side": "buy", "price": 1e9, "qty": 0.001,
             "quote": 1e6, "time": 5},
            {"id": "8", "side": "sell", "price": 1.1e9, "qty": 0.001,
             "quote": 1.1e6, "time": 6}]
    monkeypatch.setattr(p.indodax_api, "my_trades", lambda pair: fake)
    assert p.sync_trades(tmp_conn, ["btc_idr"]) == 2
    assert p.sync_trades(tmp_conn, ["btc_idr"]) == 0
    rows = ps.list_trades(tmp_conn)
    assert {r["external_id"] for r in rows} == {"btc_idr:7", "btc_idr:8"}
    assert {r["source"] for r in rows} == {"api"}
