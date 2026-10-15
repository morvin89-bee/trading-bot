"""Tes model proposal & teks konfirmasi — tidak pernah membuka jaringan.

Delapan field wajib pesan konfirmasi (spec §6.2) dan dua pagar yang
menggantungkannya: kadaluarsa jendela dan geser harga > 0,5% (§6.2/§6.3).
"""
import string

import proposal
import rules
import safeguards


def _act(kind="BUY", pair="btc_idr", amount=275000, price=1000.0):
    return rules.Action(kind, pair, amount, amount / price, price, "alasan uji", True)


def test_confirmation_text_carries_all_eight_fields():
    p = proposal.new_proposal(_act(), rule="Take-profit +50%", reserve_after=150000,
                              window_s=90, now=0.0)
    t = proposal.confirmation_text(p)
    # qty = 275.000 / 1.000 = 275.0  (bukan 2750.0 — gagal di sini berarti
    # implementasi salah, bukan ekspektasi salah)
    for expected in ("btc_idr", "BELI", "Rp 275.000", "275.0",
                     "1.000", "Take-profit +50%", "0,2%", "Rp 150.000"):
        assert expected in t, expected
    # "275.0" juga muncul di dalam "Rp 275.000", jadi pastikan baris jumlah
    # benar-benar memakai qty — bukan kebetulan lulus karena nominalnya.
    assert "Jumlah: 275.0" in t


def test_expires_at_window_boundary():
    p = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=100.0)
    assert not proposal.is_expired(p, 100 + 89)
    assert proposal.is_expired(p, 100 + 91)
    # tepat 90 detik sudah hangus — konservatif, sesuai "ragu = tidak kirim"
    assert proposal.is_expired(p, 100 + 90)


def test_price_drift_over_half_percent_hangs_confirmation():
    p = proposal.new_proposal(_act(price=1000.0), rule="r", reserve_after=0,
                              window_s=90, now=0.0)
    assert proposal.price_still_valid(p, 1004.0)   # +0,4% -> masih valid
    assert proposal.price_still_valid(p, 1005.0)   # tepat +0,5% -> masih valid
    assert not proposal.price_still_valid(p, 1006.0)  # +0,6% -> hangus


def test_missing_price_is_invalid_not_zero():
    # ADAPTASI (dilaporkan ke controller): rencana memakai helper
    # `p_with_price(1000.0)` yang tidak didefinisikan di mana pun —
    # dipakai `new_proposal` langsung, maksudnya sama.
    p = proposal.new_proposal(_act(price=1000.0), rule="r", reserve_after=0,
                              window_s=90, now=0.0)
    assert proposal.price_still_valid(p, None) is False
    # harga terbaca Rp 0 = data rusak, bukan harga (spec §6.3)
    assert proposal.price_still_valid(p, 0.0) is False


def test_sell_is_labelled_jual():
    p = proposal.new_proposal(_act(kind="SELL"), rule="r", reserve_after=0,
                              window_s=90, now=0.0)
    t = proposal.confirmation_text(p)
    assert "JUAL" in t and "BELI" not in t


def test_new_proposal_carries_action_and_metadata():
    p = proposal.new_proposal(_act(), rule="DCA bulanan", reserve_after=42000,
                              window_s=proposal.DCA_WINDOW_S, now=5.0)
    assert p.kind == "BUY" and p.pair == "btc_idr"
    assert p.amount_idr == 275000 and p.qty == 275.0 and p.price_ref == 1000.0
    assert p.rule == "DCA bulanan" and p.reserve_after == 42000
    assert p.window_s == 86400 and p.created_ts == 5.0
    assert p.status == "proposed"
    # id tiap usulan dibahas terpisah di
    # test_id_sub_second_distinct_and_client_order_id_safe
    q = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=6.0)
    assert q.id != p.id


def test_id_sub_second_distinct_and_client_order_id_safe():
    # Dua tick dalam detik YANG SAMA (mis. apply_ulang vs denyut jantung)
    # tidak boleh berbagi satu jangkar tombol Telegram / kunci log.
    a = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=100.0)
    b = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=100.5)
    assert a.id != b.id
    # `now` yang identik -> id yang identik (disengaja, demi determinisme tes)
    c = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=100.0)
    assert c.id == a.id
    # client_order_id: <=36 karakter dari [0-9A-Za-z_-] — kasus terpanjang
    # yang realistis: kind SELL + pasangan terpanjang + mikrodetik penuh
    d = proposal.new_proposal(
        rules.Action("SELL", "matic_idr", 275000, 1.0, 100.0, "uji", True),
        rule="r", reserve_after=0, window_s=90, now=1760550000.123456)
    assert len(d.id) <= 36
    assert set(d.id) <= set(string.ascii_letters + string.digits + "_-")


def test_qty_line_never_uses_scientific_notation():
    # Rp 275.000 pada aset mahal menghasilkan qty di bawah 0,0001 —
    # "3e-05" tidak terbaca orang awam di pesan yang memutuskan uang.
    p = proposal.new_proposal(_act(amount=300, price=10_000_000.0),
                              rule="r", reserve_after=0, window_s=90, now=0.0)
    t = proposal.confirmation_text(p)
    assert p.qty == 3e-05                 # angka yang memang diusulkan
    assert "Jumlah: 0.00003 koin" in t    # bukan "3e-05"
    assert "e-" not in t


def test_constants_match_spec():
    # angka spesifikasi yang mengikat: jendela reaktif 90 dtk, DCA 24 jam,
    # geser harga 0,5%
    assert proposal.REACTIVE_WINDOW_S == 90
    assert proposal.DCA_WINDOW_S == 86400
    assert proposal.PRICE_DRIFT_TOL == 0.005


def test_rule_spelling_agrees_with_safeguards_reserve_gate():
    # kegagalan diam-diam: ejaan "reserve_buy" yang berbeda akan jatuh ke
    # gerbang saldo DCA dan menolak reserve buy yang sah (ruling F15)
    a = rules.Action("BUY", "btc_idr", 25000, 25.0, 1000.0, "reserve buy", True)
    p = proposal.new_proposal(a, rule=proposal.RULE_RESERVE_BUY, reserve_after=25000,
                              window_s=proposal.REACTIVE_WINDOW_S, now=0.0)
    out = safeguards.gates(a, free_idr=0, reserve_idr=50000, today_counts={},
                           last_fill_ts={}, held_pairs_24h=set(), now=0.0,
                           rule=p.rule)
    assert out == []
