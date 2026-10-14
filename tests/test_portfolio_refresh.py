import portfolio as p
import portfolio_store as ps


def test_refresh_handles_missing_price(tmp_conn, monkeypatch):
    monkeypatch.setattr(p, "fetch_balances", lambda: {"btc": 0.001, "IDR": 200000})
    monkeypatch.setattr(p, "fetch_price", lambda pair: None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    out = p.refresh(tmp_conn)
    assert out["total_idr"] == 200000          # harga tak tersedia -> nilai aset 0
    assert len(ps.list_snapshots(tmp_conn)) == 1
