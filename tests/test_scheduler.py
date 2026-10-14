import scheduler


def test_take_snapshot_writes_row(tmp_conn, monkeypatch):
    monkeypatch.setattr(scheduler.portfolio, "refresh",
                        lambda conn, online=True: {"holdings": [],
                                                   "total_idr": 0, "actions": []})
    monkeypatch.setattr(scheduler.portfolio_store, "insert_snapshot",
                        lambda conn, snap: 99)
    out = scheduler.take_snapshot(tmp_conn)
    assert out["total_idr"] == 0
