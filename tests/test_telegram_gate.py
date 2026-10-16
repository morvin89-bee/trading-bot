import proposal
import rules

def _proposal():
    # Create a dummy proposal for testing
    action = rules.Action(
        kind="BUY",
        pair="btc_idr",
        amount_idr=50000,
        qty=0.001,
        price_ref=100000000,
        reason="test reason",
        executable=True,
    )
    return proposal.new_proposal(
        action,
        rule="reserve_buy",
        reserve_after=50000,
        window_s=90,
        now=1_000_000_000,
    )


def test_confirmation_has_three_buttons_and_no_approve_all():
    captured = {}
    def fake_send(token, chat_id, text, reply_markup=None):
        captured.update(text=text, markup=reply_markup)
        return 42
    import telegram_gate
    tg = telegram_gate.Telegram("t", "c", send=fake_send)
    assert tg.confirmation(_proposal()) == 42
    labels = [b["text"] for row in captured["markup"]["inline_keyboard"] for b in row]
    assert labels == ["Setuju", "Tunda", "Batal bulan ini"]
    assert "semua" not in " ".join(labels).lower()


def test_expiry_notifies_once_in_one_line():
    sent = []
    import telegram_gate
    tg = telegram_gate.Telegram(
        "t", "c", send=lambda *a, **k: sent.append(a) or True)
    tg.line("BTC konfirmasi kadaluarsa â€” tidak ada order dikirim.")
    assert len(sent) == 1 and "\n" not in sent[0][2]


def test_line_returns_false_rather_than_raising():
    # gagal kirim = diam, bukan menabrak loop
    import telegram_gate
    tg = telegram_gate.Telegram("t", "c", send=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert tg.line("x") is False