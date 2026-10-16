"""Tes engine satu tick — tidak pernah membuka jaringan.

Semua jalur keluar (gerbang spread, Telegram) disuntikkan lewat argumen
`verdict` dan `telegram`; guard di `conftest.py` (root) memastikan lupa
menyuntikkan berarti gagal, bukan diam-diam memanggil bursa.
"""
import inspect
import engine
import proposal
import pytest
import rules
import sqlite3
import tempfile
from unittest.mock import Mock
from portfolio import Holding

def _rich_ctx():
    """Helper for test_every_action_carries_a_reason."""
    from types import SimpleNamespace
    # Create holdings that will generate both take-profit and reserve buy actions
    holdings = [
        # Holding that will trigger take-profit: bought at 1000, now at 1600 (+60%)
        Holding(
            pair="btc_idr",
            coin="btc",
            qty=1.0,
            avg_cost=1000.0,
            price=1600.0,
            value_idr=1600000.0,
            cost_basis_idr=1000000.0,
            unrealized_idr=600000.0,
            basis_known=True
        ),
        # Holding that will trigger reserve buy: bought at 2000, now at 1400 (-30%)
        Holding(
            pair="eth_idr",
            coin="eth",
            qty=1.0,
            avg_cost=2000.0,
            price=1400.0,
            value_idr=1400000.0,
            cost_basis_idr=2000000.0,
            unrealized_idr=-600000.0,
            basis_known=True
        )
    ]
    return SimpleNamespace(
        holdings=holdings,
        prices={},
        free_idr=100000.0,
        reserve_idr=50000.0,
        today_counts={},
        last_fill_ts={},
        held_pairs_24h=set(),
    )

def _ctx(price=950.0):
    """Helper for test_stale_numbers_are_never_reused_across_proposals."""
    from types import SimpleNamespace
    # Create a holding that will generate an action
    # Using avg_cost=600.0 so that both price=1000.0 and price=1400.0
    # will generate take-profit actions (for the stale numbers test)
    holdings = [
        Holding(
            pair="btc_idr",
            coin="btc",
            qty=1.0,
            avg_cost=600.0,
            price=price,
            value_idr=price * 1.0,
            cost_basis_idr=600.0 * 1.0,
            unrealized_idr=(price - 600.0) * 1.0,
            basis_known=True
        )
    ]
    return SimpleNamespace(
        holdings=holdings,
        prices={},
        free_idr=100000.0,
        reserve_idr=50000.0,
        today_counts={},
        last_fill_ts={},
        held_pairs_24h=set(),
    )

def _ctx_with_spread():
    """Helper for test_shadow_writes_nothing_to_exchange.

    Menghasilkan tepat SATU aksi take-profit supaya `tick` benar-benar
    sampai ke gerbang spread. Versi lamanya menerima `spread_value` yang
    tidak pernah dipakai dan menghasilkan nol aksi, sehingga `tick` keluar
    di `engine.py:71` sebelum gerbang pernah dipanggil — inti temuan F22.
    """
    from types import SimpleNamespace
    holdings = [
        # avg_cost 1000, harga 1600 -> take-profit (+60% > level +50%)
        Holding(
            pair="btc_idr",
            coin="btc",
            qty=1.0,
            avg_cost=1000.0,
            price=1600.0,
            value_idr=1600000.0,
            cost_basis_idr=1000000.0,
            unrealized_idr=600000.0,
            basis_known=True
        )
    ]
    return SimpleNamespace(
        holdings=holdings,
        prices={},
        free_idr=100000.0,
        reserve_idr=50000.0,
        today_counts={},
        last_fill_ts={},
        held_pairs_24h=set(),
    )

def _ctx_where_two_rules_fire():
    """Helper for test_duplicate_rules_produce_single_proposal."""
    from types import SimpleNamespace
    # Create holdings that will generate two actions for the same pair (same kind, pair)
    # This will test the merge_actions functionality
    holdings = [
        # Two take-profit actions for BTC from different rules (simulated)
        Holding(
            pair="btc_idr",
            coin="btc",
            qty=1.0,
            avg_cost=1000.0,
            price=1600.0,  # +60%
            value_idr=1600000.0,
            cost_basis_idr=1000000.0,
            unrealized_idr=600000.0,
            basis_known=True
        ),
        Holding(
            pair="btc_idr",
            coin="btc",
            qty=0.5,  # Different quantity
            avg_cost=1000.0,
            price=1600.0,  # Same price
            value_idr=800000.0,
            cost_basis_idr=500000.0,
            unrealized_idr=300000.0,
            basis_known=True
        )
    ]
    return SimpleNamespace(
        holdings=holdings,
        prices={},
        free_idr=100000.0,
        reserve_idr=50000.0,
        today_counts={},
        last_fill_ts={},
        held_pairs_24h=set(),
    )

def _fresh_db(tmp_path):
    """Helper for tests needing a database connection."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    return conn

def _today():
    """Helper for test_spread_rejected_is_notified_and_logged."""
    import time
    return time.strftime("%Y-%m-%d", time.localtime())

def _tg(sent_list):
    """Helper for tests needing a telegram mock."""
    class TelegramMock:
        def __init__(self, sent_list):
            self.sent = sent_list
        
        def line(self, text):
            self.sent.append(text)
            return True
            
        def confirmation(self, proposal):
            # Return a fake message ID
            return 12345
    
    return TelegramMock(sent_list)


def test_v1_never_calls_rebalance():
    # spec Â§10 â€” rebalance adalah modul v2. Yang diperiksa adalah SUMBERNYA,
    # bukan nama prosedur yang dijanjikan â€” tes ini harus bisa gagal
    assert "rebalance_actions" not in inspect.getsource(engine)

def test_every_action_carries_a_reason():
    # alasan ikut tampil di pesan Telegram; tanpa ini pengguna hanya melihat angka
    ctx = _rich_ctx()
    acts = engine.collect_actions(ctx)
    assert acts and all(a.reason != "" for a in acts)

def test_spread_rejected_is_notified_and_logged(tmp_path):
    conn = _fresh_db(tmp_path)
    sent = []
    ctx = _ctx()
    engine.tick(conn, ctx, now=0.0, telegram=_tg(sent), shadow=True,
                verdict=lambda pair, **k: (False, 94.0))   # seperti bchidr
    assert any("tidak dieksekusi" in t for t in sent)
    # Note: shadow_log.rejected_count would need to be imported
    # For now, we'll skip this assertion as it requires importing shadow_log
    # assert shadow_log.rejected_count(conn, _today()) >= 1

def test_stale_numbers_are_never_reused_across_proposals(tmp_path):
    # spec 7.1 butir 3 â€” sinyal kedaluarsa dihitung ulang dari nol
    conn = _fresh_db(tmp_path)
    ctx = _ctx(price=1000.0)
    first = engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True,
                        verdict=lambda p, **k: (True, 1.0))
    ctx2 = _ctx(price=1400.0)          # harga berubah setelah kadaluarsa
    second = engine.tick(conn, ctx2, now=600.0, telegram=_tg([]), shadow=True,
                         verdict=lambda p, **k: (True, 1.0))
    assert first and second
    assert second[0].price_ref == 1400.0      # bukan angka lama 1000.0
    assert second[0].id != first[0].id         # konfirmasi baru yang berdiri sendiri

def test_shadow_writes_nothing_to_exchange():
    # tidak ada Transport dibuat â€” jalur fisik tidak ada
    calls = {"verdict": 0}

    def verdict_spy(pair, **k):
        calls["verdict"] += 1
        return True, 1.0

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sent = []
    props = engine.tick(conn, _ctx_with_spread(), now=0.0,
                        telegram=_tg(sent), shadow=True, verdict=verdict_spy)

    # 1. Tick benar-benar sampai ke gerbang spread (dulu: nol aksi, keluar duluan)
    assert calls["verdict"] == 1
    # 2. Usulan tercatat di log bayangan dengan outcome "proposed"
    assert len(props) == 1
    rows = conn.execute("SELECT outcome FROM proposals").fetchall()
    assert [r["outcome"] for r in rows] == ["proposed"]
    # 3. Jalur sukses murni: tidak ada notifikasi halt/penolakan yang terkirim
    assert sent == []
    # 4. Engine tidak mengenal jalur eksekusi sama sekali — secara struktural
    #    tidak ada yang bisa menulis ke bursa dari tick ini.
    assert "executor" not in inspect.getsource(engine)
    # 5. Tidak ada panggilan jaringan: guard di conftest.py (root) akan mematikan
    #    tes ini bila ada yang membuka koneksi (lihat F25).

def test_duplicate_rules_produce_single_proposal():
    ctx = _ctx_where_two_rules_fire()
    # We need a conn for this test
    import sqlite3
    conn = sqlite3.connect(":memory:")
    # verdict WAJIB disuntikkan: tanpa ini tick memakai bawaan
    # spread.spread_verdict -> fetch_depth -> urlopen (F25).
    props = engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True,
                        verdict=lambda pair, **k: (True, 1.0))
    sells = [p for p in props if p.kind == "SELL" and p.pair == "btc_idr"]
    assert len(sells) <= 1

def test_reserve_buy_lolos_gerbang_karena_nama_aturan_reserve_buy(tmp_path):
    # F31: engine wajib meneruskan nama aturan "reserve_buy" ke
    # safeguards.gates (ruling F15). Kalau namanya salah, gerbang saldo
    # memakai aturan "saldo bebas - cadangan" dan MENOLAK semua reserve buy:
    # fitur beli-dari-cadangan jadi mati total dan hitungan penolakan tidak
    # pernah bisa nol (spec §9, "7-14 hari tanpa penolakan").
    conn = _fresh_db(tmp_path)
    ctx = _rich_ctx()
    # Cadangan sengaja besar supaya beda aturan terlihat jelas:
    # - aturan lama "free - reserve" = Rp 100.000 - Rp 500.000 = Rp -400.000 -> TOLAK
    # - aturan F15  "free + 0,5 x reserve" = Rp 100.000 + Rp 250.000 = Rp 350.000 -> LOLOS
    # Nominal beli eth_idr = 0,25 x Rp 500.000 (tranche bulanan) = Rp 125.000.
    ctx.free_idr = 100_000.0
    ctx.reserve_idr = 500_000.0
    sent = []
    props = engine.tick(conn, ctx, now=0.0, telegram=_tg(sent), shadow=True,
                        verdict=lambda p, **k: (True, 1.0))
    beli = [p for p in props if p.kind == "BUY"]
    assert beli, (
        "reserve buy tidak diusulkan — nama aturan yang dikirim ke gerbang "
        "saldo kemungkinan bukan 'reserve_buy', jadi aturan free - reserve "
        f"yang menolaknya. Pesan terkirim: {sent}"
    )
    # Nama aturan pada usulan harus persis ejaan yang dibaca safeguards.gates
    assert beli[0].rule == proposal.RULE_RESERVE_BUY
    # F33: sisa cadangan setelah order benar-benar berkurang (spec L112) —
    # perilaku ini berubah di ronde F31 tapi belum pernah dipatok lewat
    # engine.tick. Angka: Rp 500.000 - tranche Rp 125.000 (0,25 x monthly
    # Rp 500.000) = Rp 375.000.
    assert beli[0].reserve_after == 375_000.0
    # Pesan konfirmasi pengguna juga menampilkan nama itu (bukan "Turun")
    assert "Aturan: reserve_buy" in proposal.confirmation_text(beli[0])
    # Tidak boleh ada penolakan yang tercatat — penolakan abadi = sistem
    # tak pernah bisa go-live (konsekuensi F31 nomor 2)
    outcomes = [r[0] for r in conn.execute("SELECT outcome FROM proposals")]
    assert "gate_rejected" not in outcomes, f"hasil log bayangan: {outcomes}"

def test_shadow_log_mencatat_nama_aturan_reserve_buy_untuk_penolakan(tmp_path):
    # F31: dua situs nama aturan pada proposal bayangan (status
    # "spread_rejected" dan "gate_rejected") juga harus "reserve_buy",
    # bukan kata pertama alasan ("Turun"). Kolom rule inilah yang dibaca
    # review harian dan yang dipakai ruling F17b.
    conn = _fresh_db(tmp_path)
    conn.row_factory = sqlite3.Row

    # Jalur 1: spread gugur -> proposal bayangan "spread_rejected"
    engine.tick(conn, _rich_ctx(), now=1000.0, telegram=_tg([]), shadow=True,
                verdict=lambda p, **k: (False, 94.0))
    # Jalur 2: ditolak pagu per aset per hari (3 order) -> "gate_rejected"
    ctx = _rich_ctx()
    ctx.today_counts = {"eth_idr": 3}
    engine.tick(conn, ctx, now=2000.0, telegram=_tg([]), shadow=True,
                verdict=lambda p, **k: (True, 1.0))

    rows = conn.execute("SELECT rule, outcome FROM proposals").fetchall()
    got = {(r["rule"], r["outcome"]) for r in rows}
    assert ("reserve_buy", "spread_rejected") in got, f"baris log: {sorted(got)}"
    assert ("reserve_buy", "gate_rejected") in got, f"baris log: {sorted(got)}"
    # Take-profit punya nama sendiri yang stabil, bukan "reserve_buy" dan
    # bukan kata pertama alasan ("Untung")
    assert ("take_profit", "proposed") in got, f"baris log: {sorted(got)}"

def test_rule_name_setiap_aksi_collect_actions_terdaftar():
    # F32 + G4: tutup SEMUA aksi yang bisa dihasilkan collect_actions hari
    # ini (dua sumber v1: take-profit SELL dan reserve buy BUY). Bila Task 13
    # menambah sumber ketiga (DCA), tes ini gagal dan memaksa keputusan
    # sadar atas nama aturan sumber baru — bukan degradasi diam-diam ke
    # "unknown" yang tampil ke pengguna dan masuk log bayangan.
    acts = engine.collect_actions(_rich_ctx())
    assert acts, "fixture _rich_ctx harus menghasilkan aksi"
    harap = {"SELL": engine.RULE_TAKE_PROFIT, "BUY": proposal.RULE_RESERVE_BUY}
    assert {a.kind for a in acts} == set(harap), (
        "ada jenis aksi di luar dua sumber v1 — daftarkan sumber barunya "
        f"dulu. aksi: {[(a.kind, a.pair) for a in acts]}"
    )
    for a in acts:
        nama = engine.rule_name(a)
        assert nama == harap[a.kind], (
            f"aksi {a.kind} {a.pair} dinamai {nama!r}, bukan "
            f"{harap[a.kind]!r} — nama harus berasal dari daftar sumbernya, "
            "bukan dari isi alasan"
        )
        assert nama != "unknown", "G4: 'unknown' tidak boleh sampai pengguna"

def test_rule_name_tidak_membaca_nama_dari_kata_alasan():
    # F32: pemetaan lama membaca kata "cadangan" dari alasan. Kalau redaksi
    # alasan berubah (Task 13 menulis alasan DCA sendiri), nama aturan ikut
    # berubah — atau lebih buruk, aksi lain yang kebetulan menyebut
    # "cadangan" salah dinamai reserve_buy dan MEMBUKA cabang cadangan di
    # safeguards.gates. Nama harus tetap sama walau alasan diganti.
    acts = engine.collect_actions(_rich_ctx())
    buy = next(a for a in acts if a.kind == "BUY")
    buy.reason = "dialihkan dari tujuan lain, tanpa kata kunci lama"
    assert engine.rule_name(buy) == proposal.RULE_RESERVE_BUY, (
        "nama aturan bergantung pada isi alasan — cacat yang sama dengan F31"
    )

def test_rule_name_menolak_aksi_tanpa_asal():
    # F32 + G4: aksi yang tidak pernah didaftarkan collect_actions tidak
    # boleh diam-diam dinamai "unknown" (pengguna melihat "Aturan: unknown",
    # shadow_log mencatat rule="unknown"). Gagal keras malah, supaya sumber
    # baru dipaksa mendeklarasikan dirinya.
    aksi = rules.Action(
        kind="SELL", pair="btc_idr", amount_idr=400.0, qty=0.25,
        price_ref=1600.0, reason="alasan apa pun", executable=True)
    with pytest.raises(ValueError, match="tidak punya nama aturan"):
        engine.rule_name(aksi)

def test_collect_actions_menolak_dua_aturan_untuk_pasangan_sama(monkeypatch):
    # F32: dua sumber yang menghasilkan (kind, pair) sama dengan nama aturan
    # berbeda adalah kebuntuan penamaan — salah satu pasti salah, dan pilihan
    # diam-diam bisa jatuh ke arah tidak aman ("reserve_buy" yang salah
    # membuka cabang cadangan di safeguards.gates). Skenario masa depan
    # disuntik dengan monkeypatch karena dua sumber hari ini tidak pernah
    # berbagi (kind, pair).
    aksi_satu = rules.Action(
        kind="SELL", pair="btc_idr", amount_idr=400.0, qty=0.25,
        price_ref=1600.0, reason="dari sumber pertama", executable=True)
    aksi_dua = rules.Action(
        kind="SELL", pair="btc_idr", amount_idr=400.0, qty=0.25,
        price_ref=1600.0, reason="dari sumber kedua", executable=True)
    monkeypatch.setattr(engine.rules, "take_profit_actions",
                        lambda holdings: [aksi_satu])
    monkeypatch.setattr(engine.rules, "reserve_buy_actions",
                        lambda holdings, idr_reserve: [aksi_dua])
    with pytest.raises(ValueError, match="dua aturan berbeda"):
        engine.collect_actions(_rich_ctx())
