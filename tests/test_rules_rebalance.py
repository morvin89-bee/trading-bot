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


def test_no_sell_when_on_target_once_reserve_is_counted():
    # BTC Rp550.000 (55% dari total Rp1.000.000 termasuk cadangan Rp450.000)
    # -> tepat di target, jadi tidak boleh disarankan jual walau take-profit.
    h = [_h("btc", "btc_idr", 1_000_000, 1_600_000, 0.34375)]
    assert [a for a in rules.rebalance_actions(h, idr_reserve=450_000)
            if a.kind == "SELL"] == []


def test_reserve_buy_suggested_when_asset_falls_15pct():
    h = [_h("btc", "btc_idr", 1_000_000, 800_000, 1.0)]   # -20% dari rata-rata
    acts = rules.reserve_buy_actions(h, idr_reserve=100000)
    assert acts and acts[0].kind == "BUY" and acts[0].pair == "btc_idr"


def test_no_reserve_buy_on_shallow_drop():
    h = [_h("btc", "btc_idr", 1_000_000, 900_000, 1.0)]   # hanya -10%
    assert rules.reserve_buy_actions(h, idr_reserve=100000) == []


def test_reserve_buy_keeps_most_of_the_reserve():
    h = [_h("btc", "btc_idr", 1_000_000, 700_000, 1.0)]
    acts = rules.reserve_buy_actions(h, idr_reserve=100000, max_fraction=0.5)
    assert acts[0].amount_idr <= 50000 + 1e-6
