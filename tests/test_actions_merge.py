"""Tes gabung aksi kembar — tidak pernah membuka jaringan.

Review Focus #4: dua aturan yang menunjuk penjualan yang sama tidak boleh
menumpuk ("jual 25% + 25% = 50%"). Saran digestabung per `(kind, pair)`
menjadi satu aksi SEBELUM pesan Telegram dibuat (spec §6.6, pewaris dedupe
`24ad5ef`).
"""
import actions_merge
import rules
from portfolio import Holding


def _holding(pair="btc_idr", qty=1.0, price=100.0, avg_cost=50.0, value=100.0):
    coin = pair.split("_")[0]
    return Holding(pair, coin, qty, avg_cost, price, value, avg_cost * qty,
                   (price - avg_cost) * qty, True)


def test_two_rules_same_pair_become_one_action():
    # Review Focus #4 — bug "jual 25% + 25% = 50%" harus mati di lapisan ini
    h = _holding(pair="btc_idr", qty=1.0, price=100.0, avg_cost=50.0, value=100.0)
    tp = rules.take_profit_actions([h])          # ambang +50% tercapai
    rb = rules.rebalance_actions([h], idr_reserve=0.0)
    merged = actions_merge.merge_actions(tp + rb)
    sells = [a for a in merged if a.kind == "SELL" and a.pair == "btc_idr"]
    assert len(sells) == 1


def test_merged_qty_counted_from_remaining_position():
    # kuantitas hasil gabungan tidak boleh melebihi saldo posisi
    a = rules.Action("SELL", "btc_idr", 999999, 5.0, 100.0, "r", True)
    b = rules.Action("SELL", "btc_idr", 999999, 5.0, 100.0, "r", True)
    merged = actions_merge.merge_actions([a, b])
    assert len(merged) == 1
    assert merged[0].qty <= 5.0


def test_buy_twins_take_largest_nominal_not_sum():
    # nominal dua sumber tidak boleh dijumlahkan — itu melanggar atap
    # per order (spec §6.4); ambil yang terbesar dan satukan alurnya
    a = rules.Action("BUY", "btc_idr", 100000, 100.0, 1000.0, "DCA bulanan", True)
    b = rules.Action("BUY", "btc_idr", 275000, 275.0, 1000.0, "reserve buy", True)
    merged = actions_merge.merge_actions([a, b])
    assert len(merged) == 1
    assert merged[0].amount_idr == 275000        # terbesar, bukan 375000
    assert merged[0].qty == 275.0                # ikut aksi nominal terbesar


def test_reasons_are_all_visible_to_the_user():
    # pesan konfirmasi wajib menyebut semua aturan pemicu (spec §6.2)
    a = rules.Action("SELL", "btc_idr", 25000, 0.25, 100.0, "untung +50%", True)
    b = rules.Action("SELL", "btc_idr", 25000, 0.25, 100.0, "rebalance", True)
    merged = actions_merge.merge_actions([a, b])
    assert merged[0].reason == "untung +50%; rebalance"


def test_singleton_passes_through_untouched():
    a = rules.Action("SELL", "btc_idr", 25000, 0.25, 100.0, "untung +50%", True)
    merged = actions_merge.merge_actions([a])
    assert merged == [a]
    assert merged[0] is a                        # dilewatkan apa adanya


def test_sell_and_buy_for_same_pair_are_not_merged():
    # penggabungan per (kind, pair): SELL dan BUY pasangan yang sama adalah
    # dua usulan berbeda — menyatukannya akan membuang salah satu niat
    sell = rules.Action("SELL", "btc_idr", 25000, 0.25, 100.0, "untung", True)
    buy = rules.Action("BUY", "btc_idr", 100000, 100.0, 1000.0, "DCA", True)
    merged = actions_merge.merge_actions([sell, buy])
    assert [(a.kind, a.pair) for a in merged] == [("SELL", "btc_idr"),
                                                  ("BUY", "btc_idr")]
