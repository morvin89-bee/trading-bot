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
