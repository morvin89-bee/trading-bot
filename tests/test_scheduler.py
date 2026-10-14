import scheduler


def test_take_snapshot_writes_row(tmp_conn, monkeypatch):
    monkeypatch.setattr(scheduler.portfolio, "refresh",
                        lambda conn, online=True: {"holdings": [],
                                                   "total_idr": 0, "actions": []})
    monkeypatch.setattr(scheduler.portfolio_store, "insert_snapshot",
                        lambda conn, snap: 99)
    out = scheduler.take_snapshot(tmp_conn)
    assert out["total_idr"] == 0


def test_main_survives_refresh_failure(tmp_conn, monkeypatch):
    monkeypatch.setattr(scheduler.portfolio_store, "connect",
                        lambda *a, **k: tmp_conn)
    monkeypatch.setattr(scheduler.portfolio_store, "init_db",
                        lambda conn: None)

    def boom(conn, online=True):
        raise RuntimeError("jaringan mati")

    monkeypatch.setattr(scheduler.portfolio, "refresh", boom)
    assert scheduler.main([]) == 1          # gagal rapi, bukan meledak
