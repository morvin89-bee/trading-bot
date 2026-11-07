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
        "rule": "",
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
    # available = free - cadangan (jalur BUY biasa, mis. DCA — ruling F15)
    a = _act(amount=100000)
    out = safeguards.gates(a, **_ok(free_idr=120000, reserve_idr=50000,
                                    rule=""))
    assert any("cadangan" in r for r in out)


def test_sell_is_never_gated_by_idr_balance():
    # ruling F15 — sell tidak membelanjakan IDR; sell MENGHASILKAN IDR.
    # Aturan `free - reserve` tanpa syarat akan menolak semua sell dan
    # melumpuhkan take-profit seluruhnya
    a = rules.Action("SELL", "btc_idr", 100000, 1.0, 100.0, "uji", True)
    out = safeguards.gates(a, **_ok(free_idr=0, reserve_idr=500000))
    assert not any("cadangan" in r or "saldo" in r for r in out)


def test_reserve_buy_may_draw_from_reserve():
    # ruling F15 — cadangan adalah SUMBER dana reserve buy, bukan penghalangnya.
    # Jumlahnya sudah dibatasi `max_fraction` (0.5) oleh `reserve_buy_actions`
    # ADAPTASI (dilaporkan ke controller): rencana menulis jumlah 30000, tapi
    # 30000 > 0,5 x 50000 = 25000 — mustahil lolos, dan `reserve_buy_actions`
    # pun tidak pernah menghasilkan 30000 dari cadangan 50000 (budget =
    # 0,5 x cadangan). Dipakai 25000 (tepat batas), sehingga tes tetap
    # membuktikan cadangan bisa dipakai sekaligus mematok batasnya.
    # `free_idr` SUDAH termasuk cadangan: saldo bebas 50.000 dengan cadangan
    # 50.000 berarti seluruh saldo adalah cadangan.
    a = rules.Action("BUY", "btc_idr", 25000, 25.0, 1000.0, "reserve buy", True)
    out = safeguards.gates(a, **_ok(free_idr=50000, reserve_idr=50000,
                                    rule="reserve_buy"))
    assert out == []


def test_reserve_buy_cannot_exceed_half_of_reserve_even_if_rule_forgets():
    # pertahanan kedua: batas `max_fraction` ditegakkan di gerbang juga,
    # supaya salah input dari engine tidak menguras cadangan
    a = rules.Action("BUY", "btc_idr", 40000, 40.0, 1000.0, "reserve buy", True)
    out = safeguards.gates(a, **_ok(free_idr=50000, reserve_idr=50000,
                                    rule="reserve_buy"))
    assert any("cadangan" in r for r in out)


def test_reserve_buy_does_not_double_count_free_idr():
    # free_idr SUDAH termasuk cadangan. Dengan free=200.000 dan reserve=100.000,
    # yang boleh dipakai = (200.000 - 100.000) + 50% x 100.000 = 150.000.
    # Rumus lama `free + 50% x reserve` = 250.000 -> order 200.000 lolos
    # padahal melewati batas 150.000.
    lolos = rules.Action("BUY", "btc_idr", 150000, 150.0, 1000.0,
                         "reserve buy", True)
    assert safeguards.gates(
        lolos, **_ok(free_idr=200000, reserve_idr=100000,
                     rule="reserve_buy")) == []
    tolak = rules.Action("BUY", "btc_idr", 200000, 200.0, 1000.0,
                         "reserve buy", True)
    out = safeguards.gates(
        tolak, **_ok(free_idr=200000, reserve_idr=100000,
                     rule="reserve_buy"))
    assert any("kurang" in r for r in out), out


def test_max_order_adalah_porsi_tetap_setoran_bulanan():
    """Atap per order tidak boleh menyimpang dari 55% setoran bulanan."""
    assert safeguards.MAX_ORDER_IDR == int(rules.MONTHLY_IDR * 0.55)


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


def test_permissions_unknown_means_halt():
    """Izin yang tidak diketahui (None) = ragu = berhenti."""
    assert safeguards.permissions_ok(None, False, False) is not None
    assert safeguards.permissions_ok(None, False, True) is not None
    assert safeguards.permissions_ok(True, None, True) is not None
    assert safeguards.permissions_ok(False, None, False) is not None
