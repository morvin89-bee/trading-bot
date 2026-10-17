"""Tes masuknya sumber DCA ke `engine` — provenance, tabrakan, jendela 24 jam.

Task 15.3: `Context.dca_enabled` (sakelar per-siklus dari `main_loop`),
pendaftaran provenance aksi DCA di `collect_actions` (F32), penyaringan
kaki reserve buy yang berbenturan dengan klaim DCA (keputusan builder #2
desain `2026-10-17-dca-bulanan-task15-design.md`), dan cabang jendela
`proposal.DCA_WINDOW_S` (86400 detik) di `engine.tick`.

Tidak ada tes di sini yang membuka jaringan: `tick` selalu menerima
`verdict` dan `telegram` suntikan (guard `conftest.py` root menjatuhkan
kelupaan dengan `JaringanDibuka`). Angka fixture diverifikasi terhadap
`rules.dca_actions`, `rules.reserve_buy_actions`, dan
`rules.take_profit_actions` yang asli — bukan angka karangan.
"""
import engine
import proposal
import rules
import safeguards
import sqlite3
from portfolio import Holding

# Harga jujur untuk ketiga pasangan target — dipakai seluruh tes di sini.
HARGA = {"btc_idr": 1600.0, "eth_idr": 1400.0, "sol_idr": 350.0}


def _tg(sent):
    """Perekam Telegram — pola yang sama dengan `_tg` di tests/test_engine.py."""
    class TelegramPerekam:
        def line(self, text):
            sent.append(text)
            return True

        def confirmation(self, p):
            return 12345        # id palsu, sama seperti TelegramMock lama

    return TelegramPerekam()


def _ctx(holdings, *, free_idr, reserve_idr, dca_enabled=False):
    """`engine.Context` nyata (bukan SimpleNamespace): field `dca_enabled`
    adalah bagian dari kontrak produksi yang sedang diuji di sini."""
    return engine.Context(
        holdings=holdings, prices=dict(HARGA), free_idr=free_idr,
        reserve_idr=reserve_idr, today_counts={}, last_fill_ts={},
        held_pairs_24h=set(), dca_enabled=dca_enabled,
    )


def _btc_take_profit():
    """btc +60% dari harga rata-rata -> tepat SATU aksi SELL take-profit."""
    return Holding(pair="btc_idr", coin="btc", qty=1.0, avg_cost=1000.0,
                   price=1600.0, value_idr=1600000.0, cost_basis_idr=1000000.0,
                   unrealized_idr=600000.0, basis_known=True)


def _eth_jatuh():
    """eth turun 30% dari rata-rata -> reserve buy menawar BUY eth_idr."""
    return Holding(pair="eth_idr", coin="eth", qty=1.0, avg_cost=2000.0,
                   price=1400.0, value_idr=1400000.0, cost_basis_idr=2000000.0,
                   unrealized_idr=-600000.0, basis_known=True)


def _btc_jatuh():
    """btc turun 30% dari rata-rata dengan nilai portofolio kecil (Rp 1.400).

    Sengaja kecil: nilai btc harus jauh DI BAWAH bobot target 55% supaya
    `dca_split` juga mengalokasikan dana ke btc_idr — pasangan yang sama
    yang diburu reserve buy (drop 30% >= ambang 15%). Tanpa ini bentrok
    yang diuji tes tabrakan tidak pernah terbukti.
    """
    return Holding(pair="btc_idr", coin="btc", qty=1.0, avg_cost=2000.0,
                   price=1400.0, value_idr=1400.0, cost_basis_idr=2000.0,
                   unrealized_idr=-600.0, basis_known=True)


def test_collect_actions_tanpa_sakelar_tidak_menghasilkan_dca():
    # Konteks baku (`dca_enabled` False): take-profit dan reserve buy tetap
    # keluar, tetapi TIDAK ada satu pun aksi ber-rule "dca_bulanan" —
    # regresi perilaku lama v1. Kontrolnya: konteks yang SAMA dengan sakelar
    # menyala menghasilkan aksi DCA, jadi ketiadaan DCA di atas pasti karena
    # sakelar, bukan karena data tidak cukup (tes tidak boleh kosong).
    ctx = _ctx([_btc_take_profit(), _eth_jatuh()], free_idr=600_000.0,
               reserve_idr=500_000.0)
    mati = engine.collect_actions(ctx)
    assert mati, "fixture harus tetap menghasilkan aksi v1 (take-profit + reserve buy)"
    assert rules.RULE_DCA not in {engine.rule_name(a) for a in mati}, (
        "tanpa sakelar, aksi DCA tidak boleh muncul")
    ctx.dca_enabled = True
    nyala = engine.collect_actions(ctx)
    assert rules.RULE_DCA in {engine.rule_name(a) for a in nyala}, (
        "kontrol: data fixture cukup untuk menghasilkan DCA bila sakelar menyala")


def test_collect_actions_menghasilkan_dca_dengan_provenance():
    # F32: aksi DCA keluar dari `collect_actions` DENGAN nama aturan
    # terdaftar — `engine.rule_name` tidak boleh melempar ValueError.
    # Portofolio kosong + saldo bebas Rp 600.000 -> ketiga pasangan target
    # kebagian (diverifikasi terhadap rules.dca_actions: btc Rp 305.000
    # terpotong atap per order menjadi Rp 275.000, eth Rp 138.000,
    # sol Rp 55.000).
    ctx = _ctx([], free_idr=600_000.0, reserve_idr=0.0, dca_enabled=True)
    acts = engine.collect_actions(ctx)
    beli = [a for a in acts if a.kind == "BUY"]
    assert {a.pair for a in beli} == {"btc_idr", "eth_idr", "sol_idr"}, (
        f"aksi DCA: {[(a.pair, a.amount_idr) for a in beli]}")
    for a in beli:
        assert engine.rule_name(a) == rules.RULE_DCA == "dca_bulanan"
        assert 0 < a.amount_idr <= safeguards.MAX_ORDER_IDR
        assert a.price_ref == HARGA[a.pair]     # harga jujur dari prices


def test_dca_mengalahkan_reserve_buy_pada_pasangan_sama():
    # Keputusan builder #2 (desain §Bagian how): DCA mengklaim lebih dulu;
    # kaki reserve buy untuk (kind, pair) yang sudah diklaim DCA dilewati DI
    # `collect_actions`. Tanpa penyaringan itu, `_catat_asal` melempar
    # ValueError ("diklaim dua aturan berbeda") yang men-halt seluruh tick —
    # termasuk take-profit yang tidak bersalah.
    ctx = _ctx([_btc_jatuh()], free_idr=600_000.0, reserve_idr=500_000.0,
               dca_enabled=True)
    # Bukti fixture benar-benar berebut: kedua sumber menawar BUY btc_idr.
    kuduan = rules.reserve_buy_actions(ctx.holdings, idr_reserve=ctx.reserve_idr)
    assert any(a.kind == "BUY" and a.pair == "btc_idr" for a in kuduan), (
        "fixture tidak menghasilkan reserve buy btc — tes ini jadi kosong")
    dca = rules.dca_actions(ctx.holdings, ctx.free_idr, ctx.prices,
                            max_order=safeguards.MAX_ORDER_IDR)
    assert any(a.kind == "BUY" and a.pair == "btc_idr" for a in dca), (
        "fixture tidak menghasilkan aksi DCA btc — bentrok tidak terbukti")
    acts = engine.collect_actions(ctx)      # TANPA ValueError
    beli_btc = [a for a in acts if a.kind == "BUY" and a.pair == "btc_idr"]
    assert len(beli_btc) == 1, (
        "harus tepat SATU BUY btc_idr keluar; seluruh aksi: "
        f"{[(engine.rule_name(a), a.pair, a.amount_idr) for a in acts]}")
    assert engine.rule_name(beli_btc[0]) == rules.RULE_DCA


def test_take_profit_tetap_paling_depan():
    # Kontrak `merge_actions`: dasar angka grup SELL adalah aksi PERTAMA
    # dalam grup input, jadi take-profit harus tetap paling depan walaupun
    # sumber DCA ikut dihitung (desain §Bagian how: "take_profit tetap
    # paling depan (kontrak merge)").
    ctx = _ctx([_btc_take_profit()], free_idr=1_000_000.0,
               reserve_idr=100_000.0, dca_enabled=True)
    acts = engine.collect_actions(ctx)
    assert acts[0].kind == "SELL", (
        "aksi pertama harus SELL take-profit; urutan keluar: "
        f"{[(a.kind, a.pair) for a in acts]}")
    assert engine.rule_name(acts[0]) == engine.RULE_TAKE_PROFIT
    assert any(engine.rule_name(a) == rules.RULE_DCA for a in acts), (
        "DCA harus benar-benar ikut andil — kalau tidak, urutan di atas kosong")


def test_tick_memberi_jendela_24_jam_untuk_dca(tmp_path):
    # Keputusan builder #1 (desain §Bagian how): usulan ber-rule DCA
    # memakai `proposal.DCA_WINDOW_S` (86400 detik); usulan rule lain tetap
    # `proposal.REACTIVE_WINDOW_S` (90 detik). 90 detik akan membakar hampir
    # semua Setuju DCA — `on_answer` menolak konfirmasi yang lewat waktu.
    conn = sqlite3.connect(str(tmp_path / "tick_dca.db"))
    ctx = _ctx([_btc_take_profit()], free_idr=1_000_000.0,
               reserve_idr=100_000.0, dca_enabled=True)
    sent = []
    props = engine.tick(conn, ctx, now=0.0, telegram=_tg(sent), shadow=True,
                        verdict=lambda pair, **k: (True, 1.0))
    dca = [p for p in props if p.rule == rules.RULE_DCA]
    lain = [p for p in props if p.rule != rules.RULE_DCA]
    assert dca and lain, (
        "tes butuh usulan DCA DAN non-DCA pada tick yang sama; keluar: "
        f"{[(p.rule, p.pair) for p in props]}, pesan: {sent}")
    assert proposal.DCA_WINDOW_S == 86400
    assert all(p.window_s == proposal.DCA_WINDOW_S for p in dca), (
        f"jendela usulan DCA: {[(p.pair, p.window_s) for p in dca]}")
    assert all(p.window_s == proposal.REACTIVE_WINDOW_S for p in lain), (
        f"jendela usulan non-DCA: {[(p.rule, p.pair, p.window_s) for p in lain]}")
    # DCA tidak membelanjakan cadangan: sisa cadangan tetap ctx.reserve_idr
    # (bukan max(0, reserve - amount) seperti jalur reserve buy).
    assert all(p.reserve_after == ctx.reserve_idr for p in dca), (
        f"reserve_after usulan DCA: {[(p.pair, p.reserve_after) for p in dca]}")


def test_konteks_lama_tanpa_field_dca_tetap_terbaca_mati():
    # Kompatibilitas mundur: helper lama di `tests/test_engine.py` membangun
    # konteks lewat `SimpleNamespace` TANPA field `dca_enabled`.
    # `collect_actions` tidak boleh AttributeError — field yang absen
    # dibaca sebagai sakelar mati, jadi seluruh tes lama tetap hijau tanpa
    # disentuh. Kontrol: bila atributnya dipasang, DCA ikut keluar.
    from types import SimpleNamespace
    ctx = SimpleNamespace(holdings=[], prices=dict(HARGA), free_idr=600_000.0,
                          reserve_idr=0.0, today_counts={}, last_fill_ts={},
                          held_pairs_24h=set())
    mati = engine.collect_actions(ctx)      # tidak boleh AttributeError
    assert rules.RULE_DCA not in {engine.rule_name(a) for a in mati}
    ctx.dca_enabled = True
    nyala = engine.collect_actions(ctx)
    assert rules.RULE_DCA in {engine.rule_name(a) for a in nyala}, (
        "kontrol: data cukup untuk DCA bila atribut sakelar ada")
