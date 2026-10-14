import portfolio as p
import portfolio_store as ps


def test_refresh_handles_missing_price(tmp_conn, monkeypatch):
    monkeypatch.setattr(p, "fetch_balances", lambda: {"btc": 0.001, "IDR": 200000})
    monkeypatch.setattr(p, "fetch_price", lambda pair: None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    out = p.refresh(tmp_conn)
    assert out["total_idr"] == 200000          # harga tak tersedia -> nilai aset 0
    assert len(ps.list_snapshots(tmp_conn)) == 1


def test_snapshot_market_idr_is_total_minus_contributions(tmp_conn, monkeypatch):
    monkeypatch.setattr(p, "fetch_balances", lambda: {"IDR": 200000})
    monkeypatch.setattr(p, "fetch_price", lambda pair: None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    ps.insert_deposit(tmp_conn, 150000, 1)
    p.refresh(tmp_conn)
    snap = ps.list_snapshots(tmp_conn)[-1]
    assert snap["market_idr"] == snap["total_idr"] - snap["contributions_idr"]


def test_refresh_does_not_duplicate_sell_for_same_pair(tmp_conn, monkeypatch):
    ps.insert_trade(tmp_conn, dict(pair="btc_idr", side="buy", price=1_000_000,
                                   qty=0.34375, quote=343750, fee=0, ts=1,
                                   source="manual", external_id=None))
    monkeypatch.setattr(p, "fetch_balances", lambda: {"btc": 0.34375, "IDR": 0})
    monkeypatch.setattr(p, "fetch_price",
                        lambda pair: 1_600_000 if pair == "btc_idr" else None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    out = p.refresh(tmp_conn)
    sells = [a for a in out["actions"] if a.kind == "SELL"
             and a.pair == "btc_idr"]
    assert len(sells) == 1     # bukan 2 (take-profit + rebalance) = "jual 50%"


def test_refresh_writes_at_most_one_snapshot_per_day(tmp_conn, monkeypatch):
    monkeypatch.setattr(p, "fetch_balances", lambda: {"IDR": 100000})
    monkeypatch.setattr(p, "fetch_price", lambda pair: None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    p.refresh(tmp_conn)
    p.refresh(tmp_conn)
    assert len(ps.list_snapshots(tmp_conn)) == 1   # buka dasbor berkali-kali aman


def test_offline_refresh_does_not_write_snapshot(tmp_conn):
    p.refresh(tmp_conn, online=False)              # hanya membaca cache
    assert ps.list_snapshots(tmp_conn) == []
