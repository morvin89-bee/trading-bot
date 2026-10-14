import rules
from portfolio import Holding


def _h(coin, pair, avg, price, qty):
    return Holding(pair, coin, qty, avg, price, qty * price, avg * qty,
                   (price - avg) * qty, True)


def test_no_sell_on_drift_without_takeprofit():
    h = [_h("btc", "btc_idr", 1000, 1050, 100.0)]
    assert [a for a in rules.rebalance_actions(h) if a.kind == "SELL"] == []


def test_sell_when_overweight_and_takeprofit():
    h = [_h("btc", "btc_idr", 1000, 1600, 100.0)]
    sells = [a for a in rules.rebalance_actions(h) if a.kind == "SELL"]
    assert sells and sells[0].pair == "btc_idr"


def test_never_emits_buy_so_reserve_is_untouched():
    h = [_h("btc", "btc_idr", 1000, 1000, 1.0)]
    assert [a for a in rules.rebalance_actions(h) if a.kind == "BUY"] == []


def test_position_size_cap():
    assert rules.max_position_ok(200000, 1000000) is True
    assert rules.max_position_ok(300000, 1000000) is False
