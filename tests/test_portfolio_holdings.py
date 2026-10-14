import portfolio as p


def test_basis_unknown_for_coin_without_trades():
    h = p.compute_holdings([], {"bch": 0.000583}, {"bch_idr": 7_500_000})
    bch = [x for x in h if x.coin == "bch"][0]
    assert bch.basis_known is False and bch.avg_cost is None
    assert bch.value_idr == 0.000583 * 7_500_000


def test_rewards_increase_qty_without_raising_basis():
    trades = [dict(pair="eth_idr", side="buy", price=40_000_000, qty=0.001,
                   quote=40_000, fee=0, ts=1),
              dict(pair="eth_idr", side="reward", price=0, qty=0.0001,
                   quote=0, fee=0, ts=2)]
    h = p.compute_holdings(trades, {"eth": 0.0011}, {"eth_idr": 45_000_000})
    eth = [x for x in h if x.coin == "eth"][0]
    assert eth.avg_cost == 40_000_000          # 40.000 / 0,001
    assert eth.cost_basis_idr == 40_000


def test_sell_above_buy_keeps_avg_cost_nonnegative():
    trades = [dict(pair="eth_idr", side="buy", price=44_439_000, qty=0.00445846,
                   quote=198_129, fee=0, ts=1),
              dict(pair="eth_idr", side="sell", price=44_850_000, qty=0.00454928,
                   quote=201_035, fee=0, ts=2)]
    h = p.compute_holdings(trades, {"eth": 0.0}, {"eth_idr": 44_750_000})
    eth = [x for x in h if x.coin == "eth"][0]
    assert eth.avg_cost >= 0 and eth.basis_known is True


def test_realized_pnl_on_profitable_sell():
    trades = [dict(pair="btc_idr", side="buy", price=1_000_000, qty=1.0,
                   quote=1_000_000, fee=0, ts=1),
              dict(pair="btc_idr", side="sell", price=1_200_000, qty=1.0,
                   quote=1_200_000, fee=0, ts=2)]
    assert p.realized_pnl(trades)["btc_idr"] == 200_000


def test_missing_price_marks_unrealized_unknown():
    trades = [dict(pair="btc_idr", side="buy", price=1_000_000, qty=1.0,
                   quote=1_000_000, fee=0, ts=1)]
    h = p.compute_holdings(trades, {"btc": 1.0}, {"btc_idr": None})[0]
    assert h.basis_known is True
    assert h.value_idr == 0.0
    assert h.unrealized_idr is None      # harga tak tersedia -> P&L tak diketahui
