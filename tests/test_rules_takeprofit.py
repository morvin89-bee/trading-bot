import pytest

import rules
from portfolio import Holding


def _h(avg, price, qty=1.0):
    return Holding("btc_idr", "btc", qty, avg, price, qty * price, avg * qty,
                   (price - avg) * qty, True)


def test_fires_cumulative_25pct_at_plus_50_net():
    a = rules.take_profit_actions([_h(1_000_000, 1_600_000)])   # net +59,7%
    assert len(a) == 1 and a[0].kind == "SELL" and a[0].executable
    assert a[0].qty == pytest.approx(0.25)


def test_fires_cumulative_50pct_at_plus_100():
    a = rules.take_profit_actions([_h(1000, 2100)])             # net +109,6%
    assert a[0].qty == pytest.approx(0.50)


def test_no_action_when_basis_unknown():
    h = Holding("bch_idr", "bch", 0.001, None, 7.5e6, 7500, None, None, False)
    assert rules.take_profit_actions([h]) == []


def test_below_min_order_not_executable():
    a = rules.take_profit_actions([_h(1000, 1600, qty=0.001)])  # 25% ~ Rp 0,4
    assert a and a[0].executable is False


def test_ambang_yang_sudah_dieksekusi_tidak_diusulkan_lagi():
    """Setelah +50% dijual, harga +59,7% bukan alasan menjual 25% lagi."""
    h = _h(1000, 1600)
    assert rules.take_profit_actions(
        [h], sudah={"btc_idr": (1000.0, 50.0)}) == []


def test_ambang_berikutnya_tetap_diusulkan_setelah_50():
    """+100% masih boleh menjual tranche berikutnya — 25% dari posisi kini,
    bukan mengulang 25% + 25% kumulatif."""
    h = _h(1000, 2100)                       # net +109,6%
    a = rules.take_profit_actions(
        [h], sudah={"btc_idr": (1000.0, 50.0)})
    assert len(a) == 1
    assert a[0].qty == pytest.approx(0.25)   # hanya tranche +100%
    assert a[0].level == 100


def test_state_take_profit_direset_saat_basis_berubah():
    """Basis biaya baru = siklus baru; ambang +50% boleh dijual lagi."""
    h = _h(1000, 1600)
    a = rules.take_profit_actions(
        [h], sudah={"btc_idr": (900.0, 50.0)})
    assert len(a) == 1 and a[0].level == 50


def test_sell_dipotong_ke_atap_dan_sisa_dilaporkan():
    """SELL 400.000 dipotong ke atap 275.000; sisa koin disebut, bukan hilang."""
    h = _h(1000, 1600, qty=1000)
    a = rules.take_profit_actions([h], max_order=275_000)
    assert len(a) == 1
    assert a[0].amount_idr == pytest.approx(275_000)
    assert a[0].qty == pytest.approx(275_000 / 1600)
    assert a[0].sisa_qty == pytest.approx(250 - 275_000 / 1600)
    assert "sisa" in a[0].reason.lower()
