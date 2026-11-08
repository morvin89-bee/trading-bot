"""Dua belas skenario ujung-ke-ujung — tanpa jaringan, jam disuntikkan.

Setiap tes menggerakkan `main_loop.tick_cycle` seperti denyut lima menit
sungguhan, lalu memeriksa order yang benar-benar keluar lewat transport
perekam. Tidak ada order yang menyentuh bursa nyata.
"""
import calendar
import time

import pytest

import dca_window
import engine
import executor
import main_loop
import portfolio
import proposal
import rules
import safeguards
import shadow_log
from portfolio import Holding
from tests.scenarios import harness as h


@pytest.fixture(autouse=True)
def _tanpa_tidur(monkeypatch):
    """Backoff status (2+5+10 dtk) tidak boleh menahan tes."""
    monkeypatch.setattr(executor.time, "sleep", lambda _s: None)


def _usulan_dca(telegram):
    return [p for p in telegram.konfirmasi if p.rule == rules.RULE_DCA]


def _jawab(pelari, aksi, p, *, now=None, revisi=0):
    pelari.fetch.updates = [h.tombol(aksi, p.id, revisi)]
    hasil = pelari.denyut(now)
    pelari.fetch.updates = []
    return hasil


# 1 ------------------------------------------------------------------------
def test_dca_bulanan_jalan_penuh(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport()
    pelari = h.Pelari(conn, telegram, transport=transport, shadow=False,
                      jam=h.ts(day=1, hour=9, minute=5))

    assert pelari.denyut() == "proposed"
    dca = _usulan_dca(telegram)
    assert len(dca) == 3
    assert dca_window.dca_state(conn, pelari.jam)[0] == "awaiting"

    for p in dca:
        _jawab(pelari, "setuju", p, now=pelari.jam + 1)

    assert len(transport.posted) == 3
    assert {o["pair"] for o in transport.posted} == {
        "btc_idr", "eth_idr", "sol_idr"}
    assert dca_window.dca_state(conn, pelari.jam + 2)[0] == "confirmed"

    sebelum = len(telegram.konfirmasi)
    pelari.denyut(pelari.jam + 86400)
    assert len(telegram.konfirmasi) == sebelum, "DCA ditawarkan ulang"


# 2 ------------------------------------------------------------------------
def test_tidak_dijawab_pengingat_lalu_dca_missed(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    pelari = h.Pelari(conn, telegram, jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()

    # 15.00 dan 21.00: satu pengingat per slot, bukan tiap denyut.
    pelari.sampai(h.ts(day=1, hour=21, minute=30))
    pengingat = telegram.pesan("Pengingat")
    assert len(pengingat) == 2, telegram.lines

    # Lewat 24 jam: dicatat dca_missed tepat sekali dan diberitahukan.
    pelari.denyut(h.ts(day=2, hour=9, minute=6))
    bulan = time.strftime("%Y-%m", time.localtime(pelari.jam))
    baris = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE id = ?",
        (f"dca_missed_{bulan}",)).fetchone()[0]
    assert baris == 1
    assert telegram.pesan("berakhir tanpa order")

    # Tidak ditawarkan ulang pada hari/bulan yang sama.
    sebelum = len(telegram.konfirmasi)
    pelari.denyut(h.ts(day=3, hour=12))
    assert len(telegram.konfirmasi) == sebelum


# 3 ------------------------------------------------------------------------
def test_batal_satu_membatalkan_semua_dan_setuju_terlambat_tanpa_order(
        tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport()
    pelari = h.Pelari(conn, telegram, transport=transport, shadow=False,
                      jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()
    dca = _usulan_dca(telegram)
    assert len(dca) == 3

    _jawab(pelari, "batal", dca[0], now=pelari.jam + 1)
    assert dca_window.dca_state(conn, pelari.jam + 1)[0] == "none"
    assert not any(p.id in main_loop._USULAN_MENUNGGU for p in dca)

    # Setuju pada usulan lain setelah Batal = id tak dikenal, tanpa order.
    _jawab(pelari, "setuju", dca[1], now=pelari.jam + 2)
    assert transport.posted == []
    assert any("sudah tidak dikenal" in s for s in telegram.lines)


# 4 ------------------------------------------------------------------------
def test_drift_harga_dan_tombol_lama(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport()
    harga = {"btc_idr": 1600.0, "eth_idr": 1400.0, "sol_idr": 350.0}
    pelari = h.Pelari(
        conn, telegram, transport=transport, shadow=False,
        ctx_fn=lambda now: h.ctx(harga=harga), jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()
    dca = _usulan_dca(telegram)
    p = dca[0]

    # Drift 1,5%: re-quote, revisi naik, belum ada order.
    harga[p.pair] = 1600.0 * 1.015
    _jawab(pelari, "setuju", p, now=pelari.jam + 1)
    assert transport.posted == []
    assert p.revisi == 1
    assert telegram.konfirmasi[-1].revisi == 1

    # Ketuk dua kali tombol LAMA (revisi 0): ditolak, tetap tanpa order.
    _jawab(pelari, "setuju", p, now=pelari.jam + 2, revisi=0)
    assert transport.posted == []

    # Tombol baru (revisi 1) mengirim tepat satu order.
    _jawab(pelari, "setuju", p, now=pelari.jam + 3, revisi=1)
    assert len(transport.posted) == 1

    # Drift 8%: konfirmasi hangus, jendela tetap terbuka.
    conn2 = h.db(tmp_path, "drift8.db")
    telegram2 = h.Telegram()
    transport2 = h.Transport()
    harga2 = dict(h.HARGA)
    pelari2 = h.Pelari(
        conn2, telegram2, transport=transport2, shadow=False,
        ctx_fn=lambda now: h.ctx(harga=harga2), jam=h.ts(day=1, hour=9, minute=5))
    pelari2.denyut()
    p2 = _usulan_dca(telegram2)[0]
    harga2[p2.pair] = p2.price_ref * 1.08
    _jawab(pelari2, "setuju", p2, now=pelari2.jam + 1)
    assert transport2.posted == []
    assert any("melebihi batas re-quote" in s for s in telegram2.lines)
    assert dca_window.dca_state(conn2, pelari2.jam + 1)[0] == "awaiting"

    # Take-profit drift 1,5%: hangus (batas reaktif tetap 0,5%).
    p3 = proposal.new_proposal(
        rules.Action("SELL", "btc_idr", 400000, 0.25, 1600.0, "take-profit",
                     True, level=50.0, basis=1000.0, qty_target=0.25),
        rule=engine.RULE_TAKE_PROFIT, reserve_after=0.0,
        window_s=proposal.REACTIVE_WINDOW_S, now=pelari.jam)
    main_loop._ingat([p3])
    ctx3 = h.ctx(harga={"btc_idr": 1600.0 * 1.015},
                 holdings=[Holding("btc_idr", "btc", 1.0, 1000.0, 1600.0,
                                   1600.0, 1000.0, 600.0, True)])
    hasil = engine.on_answer(
        conn, ctx3, p=p3, answer="setuju", now=pelari.jam + 1,
        transport=transport2, telegram=telegram2, shadow=False,
        verdict=h.lolos)
    assert hasil == "ignored"
    assert transport2.posted == [], "TP drift tidak boleh mengirim order"


# 5 ------------------------------------------------------------------------
def test_telegram_mati_pulih_40_menit_atau_sehari(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    telegram.gagal_kirim = True
    pelari = h.Pelari(conn, telegram, jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()
    assert _usulan_dca(telegram) == []
    assert conn.execute(
        "SELECT last_dca_date FROM bot_state WHERE id = 1").fetchone()[0] is None

    # Pulih 30 menit kemudian: percobaan kedua terkirim.
    telegram.gagal_kirim = False
    pelari.denyut(h.ts(day=1, hour=9, minute=35))
    assert len(_usulan_dca(telegram)) == 3

    # Skenario kedua: Telegram mati sampai percobaan habis.
    conn2 = h.db(tmp_path, "mati_sehari.db")
    telegram2 = h.Telegram()
    telegram2.gagal_kirim = True
    pelari2 = h.Pelari(conn2, telegram2, jam=h.ts(day=1, hour=9, minute=5))
    pelari2.sampai(h.ts(day=1, hour=21, minute=30))
    assert telegram2.pesan("berakhir tanpa order"), telegram2.lines
    baris = conn2.execute(
        "SELECT dca_status FROM bot_state WHERE id = 1").fetchone()
    assert baris["dca_status"] == "batal"

    # Sehari kemudian tetap tertutup dan tidak ada pemberitahuan kedua.
    jumlah = len(telegram2.pesan("berakhir tanpa order"))
    pelari2.denyut(h.ts(day=2, hour=12))
    assert len(telegram2.pesan("berakhir tanpa order")) == jumlah
    assert _usulan_dca(telegram2) == []


# 6 ------------------------------------------------------------------------
def test_restart_di_tengah_jendela_memulihkan_usulan_offset_dan_status(
        tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport()
    pelari = h.Pelari(conn, telegram, transport=transport, shadow=False,
                      jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()
    dca = _usulan_dca(telegram)
    assert len(dca) == 3
    offset = main_loop._OFFSET_TELEGRAM["offset"]

    dipulihkan = h.mulai_ulang(conn, pelari.jam + 60)
    assert set(dipulihkan) == {p.id for p in dca}
    assert main_loop._OFFSET_TELEGRAM["offset"] == offset
    assert dca_window.dca_state(conn, pelari.jam + 60)[0] == "awaiting"

    # Tombol dari proses lama tetap berlaku; tidak ada usulan ganda.
    _jawab(pelari, "setuju", dca[0], now=pelari.jam + 61)
    assert len(transport.posted) == 1
    jumlah_proposed = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE outcome = 'proposed'"
    ).fetchone()[0]
    pelari.denyut(pelari.jam + 3600)
    assert conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE outcome = 'proposed'"
    ).fetchone()[0] == jumlah_proposed


# 7 ------------------------------------------------------------------------
def test_harga_basi_lebih_120_detik_tidak_menghasilkan_usulan(
        tmp_path, monkeypatch):
    sekarang = 1_000_000.0
    monkeypatch.setitem(portfolio._PRICE_CACHE, "btc_idr",
                        (sekarang - 121, 1600.0))
    assert portfolio.cached_price("btc_idr", now=sekarang) is None
    assert portfolio.cached_price("btc_idr", now=sekarang - 120) == 1600.0

    conn = h.db(tmp_path)
    telegram = h.Telegram()
    pelari = h.Pelari(
        conn, telegram, jam=h.ts(day=1, hour=9, minute=5),
        verdict=lambda pair, **k: (None, 0.0))
    pelari.denyut()
    assert telegram.konfirmasi == []
    assert telegram.pesan("tidak tersedia")

    # Harga segar: usulan kembali muncul.
    pelari.verdict = h.lolos
    pelari.denyut(pelari.jam + 300)
    assert len(_usulan_dca(telegram)) == 3


# 8 ------------------------------------------------------------------------
def test_spread_none_atau_lebar_saat_setuju_tombol_tetap_bisa_dipakai(
        tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport()
    pelari = h.Pelari(conn, telegram, transport=transport, shadow=False,
                      jam=h.ts(day=1, hour=9, minute=5))
    pelari.denyut()
    p = _usulan_dca(telegram)[0]

    pelari.verdict = lambda pair, **k: (None, 0.0)
    _jawab(pelari, "setuju", p, now=pelari.jam + 1)
    assert transport.posted == []
    assert p.id in main_loop._USULAN_MENUNGGU
    assert shadow_log.outcome(conn, p.id) != "setuju"

    pelari.verdict = lambda pair, **k: (False, 99.0)
    _jawab(pelari, "setuju", p, now=pelari.jam + 2)
    assert transport.posted == []
    assert p.id in main_loop._USULAN_MENUNGGU

    pelari.verdict = h.lolos
    _jawab(pelari, "setuju", p, now=pelari.jam + 3)
    assert len(transport.posted) == 1


# 9 ------------------------------------------------------------------------
def test_take_profit_pemicu_atap_tranche_dan_isolasi_memori(
        tmp_path, monkeypatch):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport(can_trade=False)
    holding = Holding(
        pair="btc_idr", coin="btc", qty=2000.0, avg_cost=1000.0, price=1600.0,
        value_idr=3_200_000.0, cost_basis_idr=2_000_000.0,
        unrealized_idr=1_200_000.0, basis_known=True)
    pelari = h.Pelari(
        conn, telegram, transport=transport, shadow=True,
        ctx_fn=lambda now: h.ctx(holdings=[holding], free_idr=0.0),
        jam=h.ts(day=10, hour=12))

    pelari.denyut()
    tp = [p for p in telegram.konfirmasi if p.rule == engine.RULE_TAKE_PROFIT]
    assert len(tp) == 1
    p = tp[0]
    assert p.level == 50.0
    assert p.amount_idr <= safeguards.MAX_ORDER_IDR
    assert p.sisa_qty > 0, "kelebihan di atas atap harus jadi tranche berikutnya"

    # Persetujuan bayangan mencatat tranche; hari yang sama tidak ada TP kedua.
    _jawab(pelari, "setuju", p, now=pelari.jam + 1)
    sebelum = len(telegram.konfirmasi)
    pelari.denyut(pelari.jam + 300)
    assert len(telegram.konfirmasi) == sebelum, "tranche harian dilanggar"

    memori = shadow_log.take_profit_state(conn, mode="shadow")
    assert ("btc_idr", 50.0) in memori
    assert shadow_log.take_profit_state(conn, mode="live") == {}, (
        "memori shadow tidak boleh bocor ke live")

    # DCA pada hari berikutnya tidak mereset memori TP.
    pelari.ctx_fn = lambda now: h.ctx(
        holdings=[holding], free_idr=rules.MONTHLY_IDR)
    pelari.denyut(h.ts(day=11, hour=9, minute=5))
    assert shadow_log.take_profit_state(conn, mode="shadow") == memori

    # Status unknown: tranche dicatat selesai, tick berikutnya tidak menjual lagi.
    class TransportUnknown(h.Transport):
        def get_order_status(self, client_order_id):
            self.queried.append(client_order_id)
            return {"status": "unknown"}

    conn2 = h.db(tmp_path, "tp_unknown.db")
    telegram2 = h.Telegram()
    t_unknown = TransportUnknown()
    p2 = proposal.new_proposal(
        rules.Action("SELL", "btc_idr", 200000, 0.125, 1600.0, "take-profit",
                     True, level=50.0, basis=1000.0, qty_target=0.125),
        rule=engine.RULE_TAKE_PROFIT, reserve_after=0.0,
        window_s=proposal.REACTIVE_WINDOW_S, now=pelari.jam)
    hasil = engine.on_answer(
        conn2, h.ctx(harga={"btc_idr": 1600.0},
                     holdings=[Holding("btc_idr", "btc", 1.0, 1000.0, 1600.0,
                                       1600.0, 1000.0, 600.0, True)]),
        p=p2, answer="setuju",
        now=pelari.jam + 1, transport=t_unknown, telegram=telegram2,
        shadow=False, verdict=h.lolos)
    assert hasil == "halted"
    assert ("btc_idr", 50.0) in shadow_log.take_profit_state(
        conn2, mode="live")
    # Memori live tidak muncul di shadow.
    assert shadow_log.take_profit_state(conn2, mode="shadow") == {}


# 10 -----------------------------------------------------------------------
def test_saldo_kurang_tanggal_1_lalu_setoran_jam_10(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    saldo = {"nilai": 100000.0}
    pelari = h.Pelari(
        conn, telegram, ctx_fn=lambda now: h.ctx(free_idr=saldo["nilai"]),
        jam=h.ts(day=1, hour=9, minute=5))

    pelari.denyut()
    assert _usulan_dca(telegram) == []
    assert len(telegram.pesan("kurang dari setoran")) == 1
    assert dca_window.dca_state(conn, pelari.jam)[0] == "none"
    assert conn.execute(
        "SELECT last_dca_date FROM bot_state WHERE id = 1").fetchone()[0] is None

    # Denyut lain sebelum setoran: pesan saldo tidak diulang.
    pelari.denyut(h.ts(day=1, hour=9, minute=30))
    assert len(telegram.pesan("kurang dari setoran")) == 1

    saldo["nilai"] = rules.MONTHLY_IDR
    pelari.denyut(h.ts(day=1, hour=10, minute=0))
    assert len(_usulan_dca(telegram)) == 3
    assert len(telegram.pesan("kurang dari setoran")) == 1


# 11 -----------------------------------------------------------------------
@pytest.mark.parametrize("can_trade,can_withdraw", [
    (True, True),        # izin tarik dana aktif
    (True, None),        # izin tarik tidak terbaca
    (None, False),       # izin trade tidak terbaca
    (True, False),       # sakelar bayangan tetapi kunci bisa trade
])
def test_izin_halt_tanpa_usulan_dan_tanpa_order(tmp_path, can_trade,
                                                can_withdraw):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    transport = h.Transport(can_trade=can_trade, can_withdraw=can_withdraw)
    pelari = h.Pelari(conn, telegram, transport=transport, shadow=True,
                      jam=h.ts(day=1, hour=9, minute=5))

    assert pelari.denyut() == "halted"
    assert telegram.konfirmasi == []
    assert transport.posted == []


# 12 -----------------------------------------------------------------------
def test_hari_terakhir_bulan_tidak_mengusulkan_dca(tmp_path):
    conn = h.db(tmp_path)
    telegram = h.Telegram()
    kini = time.localtime()
    hari_akhir = calendar.monthrange(kini.tm_year, kini.tm_mon)[1]
    pelari = h.Pelari(conn, telegram,
                      jam=h.ts(day=min(hari_akhir, 28), hour=12))
    # Pakai tanggal terakhir sebenarnya; kalau >28, bulan berjalan tetap
    # dihitung benar oleh kalender, bukan oleh helper `ts` yang dibatasi 28.
    pelari.jam = time.mktime(time.struct_time(
        (kini.tm_year, kini.tm_mon, hari_akhir, 12, 0, 0, 0, 0, -1)))

    pelari.denyut()
    assert _usulan_dca(telegram) == []
    assert conn.execute(
        "SELECT last_dca_date FROM bot_state WHERE id = 1").fetchone()[0] is None
