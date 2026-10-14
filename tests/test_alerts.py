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
