"""Tes presisi qty di engine: bulat ke bawah, minimum, dan halt metadata."""
import sqlite3
from types import SimpleNamespace

import engine
import pair_meta
import rules
from portfolio import Holding


def _conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "qty.db"))


def _ctx(price=1600.0, qty=1.0):
    h = Holding(
        "btc_idr", "btc", qty, 1000.0, price, price * qty,
        qty * 1000.0, (price - 1000.0) * qty, True)
    return SimpleNamespace(
        holdings=[h], prices={}, free_idr=100000.0, reserve_idr=50000.0,
        today_counts={}, last_fill_ts={}, held_pairs_24h=set())


def _meta(pair, *, increment="1e-08", min_base=0.0, min_traded=0.0,
          fetched_ts=0.0):
    return pair_meta.Meta(pair, increment, min_base, min_traded, 0.002,
                          fetched_ts)


class _Tg:
    def __init__(self):
        self.lines = []
        self.konfirmasi = []

    def line(self, teks):
        self.lines.append(teks)
        return True

    def confirmation(self, prop):
        self.konfirmasi.append(prop)
        return 12345


def test_tick_membulatkan_qty_ke_bawah_dan_menghitung_ulang_nominal(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        pair_meta, "get",
        lambda conn, pair, *, now, max_age_s=pair_meta.MAX_AGE_S:
            _meta(pair, increment="1e-01"))
    tg = _Tg()

    props = engine.tick(_conn(tmp_path), _ctx(), now=0.0, telegram=tg,
                        shadow=True, verdict=lambda pair, **k: (True, 1.0))

    jual = [p for p in props if p.kind == "SELL"]
    assert len(jual) == 1
    # 25% dari 1 koin = 0,25; presisi bursa 0,1 -> 0,2 (tidak pernah 0,3).
    assert jual[0].qty == 0.2
    assert jual[0].amount_idr == 320.0


def test_tick_menolak_order_di_bawah_minimum_bursa(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pair_meta, "get",
        lambda conn, pair, *, now, max_age_s=pair_meta.MAX_AGE_S:
            _meta(pair, min_base=10000.0))
    tg = _Tg()

    props = engine.tick(_conn(tmp_path), _ctx(), now=0.0, telegram=tg,
                        shadow=True, verdict=lambda pair, **k: (True, 1.0))

    assert props == []
    assert tg.konfirmasi == []
    assert any("minimum" in s for s in tg.lines), tg.lines


def test_tick_halt_bila_metadata_pair_hilang(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pair_meta, "get",
        lambda conn, pair, *, now, max_age_s=pair_meta.MAX_AGE_S: None)
    tg = _Tg()

    props = engine.tick(_conn(tmp_path), _ctx(), now=0.0, telegram=tg,
                        shadow=True, verdict=lambda pair, **k: (True, 1.0))

    assert props == []
    assert tg.konfirmasi == []
    assert any("Metadata" in s for s in tg.lines), tg.lines


def test_payload_qty_kecil_titik_tetap_bukan_notasi_ilmiah():
    import executor
    import proposal
    aksi = rules.Action("BUY", "btc_idr", 300.0, 5e-05, 6_000_000.0,
                        "uji qty kecil", True)
    p = proposal.new_proposal(aksi, rule="r", reserve_after=0.0,
                              window_s=90, now=0.0)

    pl = executor.payload_for(p, "cid")

    assert pl["amount"] == "0.00005"
    assert "e" not in str(pl["amount"]).lower()
