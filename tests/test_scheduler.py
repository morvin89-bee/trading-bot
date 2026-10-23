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


def test_summary_menampilkan_idr():
    """IDR (uang tunai) turun baris di ringkasan Telegram."""
    from types import SimpleNamespace

    out = {"holdings": [SimpleNamespace(coin="btc", value_idr=3135.0)],
           "total_idr": 503135.0, "actions": []}
    teks = scheduler._summary(out)

    assert "Snapshot portofolio: Rp 503.135" in teks
    assert "  IDR  Rp 500.000" in teks         # turunan total - aset
    assert "  BTC  Rp 3.135" in teks

    # kolom penjumlah tetap benar: IDR + aset == total
    angka = [float(l.split("Rp ")[1].replace(".", "")) for l in teks.splitlines()
             if l.startswith("  ") and "Rp" in l]
    assert sum(angka) == out["total_idr"]
