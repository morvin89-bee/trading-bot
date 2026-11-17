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


def test_fetch_price_tolak_cache_lebih_dari_120_detik(monkeypatch):
    """Cache 121 detik = hilang; 60 detik masih sah. Batas dari konstanta."""
    monkeypatch.setattr(p, "_PRICE_CACHE", {"btc_idr": (1000.0, 500.0)})
    monkeypatch.setattr(p.price_feed, "indodax_price",
                        lambda pair: (None, None))

    assert p.fetch_price("btc_idr", now=1000.0 + 121) is None
    assert p.fetch_price("btc_idr", now=1000.0 + 60) == 500.0


def test_offline_refresh_does_not_write_snapshot(tmp_conn):
    p.refresh(tmp_conn, online=False)              # hanya membaca cache
    assert ps.list_snapshots(tmp_conn) == []


def _eth_jatuh(tmp_conn, monkeypatch):
    """ETH 20% di bawah harga rata-rata, uang tunai Rp 100.000."""
    ps.insert_trade(tmp_conn, dict(pair="eth_idr", side="buy", price=1000,
                                   qty=1, quote=1000, fee=0, ts=1,
                                   source="manual", external_id=None))
    monkeypatch.setattr(p, "fetch_balances",
                        lambda: {"eth": 1.0, "IDR": 100000})
    monkeypatch.setattr(p, "fetch_price",
                        lambda pair: 800.0 if pair == "eth_idr" else None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)


def test_refresh_alert_beli_saat_turun_memakai_cadangan(tmp_conn, monkeypatch):
    """Setengah cadangan, bukan setengah seluruh uang tunai."""
    _eth_jatuh(tmp_conn, monkeypatch)
    monkeypatch.setattr(p, "reserve_idr", lambda: 25000.0)
    out = p.refresh(tmp_conn)
    beli = [a for a in out["actions"]
            if a.kind == "BUY" and a.pair == "eth_idr"]
    assert len(beli) == 1
    assert beli[0].amount_idr == 12500.0


def test_refresh_tanpa_cadangan_tidak_menyarankan_beli_saat_turun(
        tmp_conn, monkeypatch):
    _eth_jatuh(tmp_conn, monkeypatch)
    monkeypatch.setattr(p, "reserve_idr", lambda: 0.0)
    out = p.refresh(tmp_conn)
    assert [a for a in out["actions"] if a.kind == "BUY"] == []
