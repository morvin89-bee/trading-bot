"""Tes gerbang spread — tidak pernah membuka jaringan.

Semua tes memakai fungsi murni `spread_verdict_from_depth` atau `fetch`
yang disuntikkan (spec 6.3 & §8).
"""
import math

import pytest

import spread


def test_spread_bps_uses_mid_not_bid():
    depth = {"buy": [["100", "1"]], "sell": [["101", "1"]]}
    assert spread.spread_bps(depth) == pytest.approx(10000 * 1 / 100.5)


def test_verdict_passes_below_threshold():
    depth = {"buy": [["100", "1"]], "sell": [["100.1", "1"]]}   # ~10 bps
    # 9.995002498... — abs diperlukan; pytest.approx(10) bawaan (rel 1e-6) TIDAK akan
    # lolos untuk selisih 0.005 (lihat ruling F9)
    assert spread.spread_verdict_from_depth(depth, max_bps=30) == (True, pytest.approx(10, abs=0.01))


def test_verdict_rejects_bch_like_spread():
    depth = {"buy": [["4655000", "1"]], "sell": [["4699000", "1"]]}  # ~94 bps
    ok, bps = spread.spread_verdict_from_depth(depth, max_bps=30)
    assert ok is False and bps == pytest.approx(94, abs=1)


def test_empty_depth_is_not_zero():
    # Harga hilang tidak boleh dinilai 0 lalu diloloskan (spec 6.3).
    # Catatan: `== (None, float("nan"))` mustahil di Python (nan != nan),
    # jadi "bukan nol, bukan lolos" dibuktikan dengan `is None` + `isnan`.
    ok, bps = spread.spread_verdict_from_depth({}, max_bps=30)
    assert ok is None and math.isnan(bps)


def test_verdict_uses_injected_fetch():
    # Engine menyuntikkan `fetch` supaya tes tidak pernah buka jaringan.
    seen = []

    def fake_fetch(pair_id):
        seen.append(pair_id)
        return {"buy": [["100", "1"]], "sell": [["100.1", "1"]]}

    ok, bps = spread.spread_verdict("btc_idr", fetch=fake_fetch)
    assert ok is True and bps == pytest.approx(10, abs=0.01)
    assert seen == ["btc_idr"]


def test_verdict_when_fetch_fails_is_not_a_pass():
    ok, bps = spread.spread_verdict("btc_idr", fetch=lambda pair_id: None)
    assert ok is None and math.isnan(bps)


def test_crossed_book_is_not_a_pass():
    # Buku terbalik (ask < bid) = data rusak -> sama seperti depth hilang:
    # berhenti, bukan lolos (ragu berarti tidak kirim).
    depth = {"buy": [["101", "1"]], "sell": [["100", "1"]]}
    ok, bps = spread.spread_verdict_from_depth(depth, max_bps=30)
    assert ok is None and math.isnan(bps)
