import alerts
import rules


def test_no_alerts_when_nothing():
    assert alerts.build_alerts([], []) == []


def test_alert_mentions_pair_and_reason():
    a = rules.Action("SELL", "btc_idr", 50000, 0.00003, 1.6e9,
                     "+59% dari rata-rata", True)
    out = alerts.build_alerts([], [a])
    assert len(out) == 1 and "btc_idr" in out[0] and "%" in out[0]


def test_alert_amount_uses_indonesian_format():
    a = rules.Action("SELL", "btc_idr", 400000, 0.25, 1.6e6,
                     "+59% dari rata-rata", True)
    out = alerts.build_alerts([], [a])
    assert "Rp 400.000" in out[0]


def test_alert_for_reserve_buy_says_beli():
    a = rules.Action("BUY", "btc_idr", 50000, 0.0000625, 8e8,
                     "Turun 20% dari rata-rata", True)
    out = alerts.build_alerts([], [a])
    assert out[0].startswith("BELI") and "Rp 50.000" in out[0]
