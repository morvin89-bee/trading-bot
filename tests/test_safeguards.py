"""Tes gerbang per-aksi — tidak pernah membuka jaringan.

Semua nilai disuntikkan lewat helper `_ok()`; `safeguards` murni
tidak menyentuh bursa (spec §6.4 & §6.6).
"""
import rules
import safeguards


def _ok(**overrides):
    """Semua argumen gerbang dengan nilai lolos, bisa ditimpa per tes."""
    args = {
        "free_idr": 10**9,
        "reserve_idr": 0,
        "today_counts": {},
        "last_fill_ts": {},
        "held_pairs_24h": set(),
        "now": 0.0,
    }
    args.update(overrides)
    return args


def _act(pair="btc_idr", amount=100000):
    return rules.Action("BUY", pair, amount, 1.0, 100.0, "uji", True)


def test_rejects_above_atap():
    a = _act(amount=300000)
    assert any("275.000" in r for r in safeguards.gates(a, **_ok()))


def test_allows_amount_exactly_at_atap():
    # Atap Rp 275.000 = nominal itu sendiri masih boleh; yang melebihi ditolak.
    assert safeguards.gates(_act(amount=275000), **_ok()) == []


def test_rejects_fourth_order_same_asset_today():
    # Review Focus #5
    a = _act()
    out = safeguards.gates(a, **_ok(today_counts={"btc_idr": 3}))
    assert any("3 order" in r for r in out)


def test_allows_fourth_order_other_asset_today():
    a = _act(pair="eth_idr")
    assert safeguards.gates(a, **_ok(today_counts={"btc_idr": 3})) == []


def test_rejects_within_cooldown_of_last_fill():
    # Jeda setelah terisi: 1799 detik ditolak, tepat 1800 detik lolos.
    a = _act()
    out = safeguards.gates(
        a, **_ok(last_fill_ts={"btc_idr": 1000.0}, now=1000.0 + 1799))
    assert any("jeda" in r for r in out)
    ok = safeguards.gates(
        a, **_ok(last_fill_ts={"btc_idr": 1000.0}, now=1000.0 + 1800))
    assert ok == []


def test_rejects_when_reserve_would_be_touched():
    # available = free - cadangan
    a = _act(amount=100000)
    out = safeguards.gates(a, **_ok(free_idr=120000, reserve_idr=50000))
    assert any("cadangan" in r for r in out)


def test_rejects_buysell_pair_within_24h():
    a = rules.Action("SELL", "btc_idr", 100000, 1.0, 100.0, "uji", True)
    out = safeguards.gates(a, **_ok(held_pairs_24h={"btc_idr"}))
    assert any("24 jam" in r for r in out)


def test_halt_when_can_withdraw_true():
    assert safeguards.permissions_ok(False, True, False) is not None


def test_halt_when_can_withdraw_true_even_in_live_mode():
    # canWithdraw True = berhenti apa pun kondisi lain (spec §6.6).
    assert safeguards.permissions_ok(True, True, True) is not None


def test_halt_when_can_trade_disagrees_with_mode():
    # bayangan aktif tetapi kunci sudah bisa trade
    assert safeguards.permissions_ok(True, False, False) is not None
    # sakelar aktif tetapi kunci tidak bisa trade
    assert safeguards.permissions_ok(False, False, True) is not None


def test_permissions_ok_when_all_agree():
    # Gerbang izin harus meloloskan keadaan sehat — bukan selalu berhenti.
    assert safeguards.permissions_ok(False, False, False) is None
    assert safeguards.permissions_ok(True, False, True) is None
