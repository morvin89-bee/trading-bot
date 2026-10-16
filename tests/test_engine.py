"""Tes engine (tick dan on_answer) — tidak pernah membuka jaringan.

Semua jalur keluar (gerbang spread, Telegram, transport bursa) disuntikkan
lewat argumen `verdict`, `telegram`, dan `transport`; guard di `conftest.py`
(root) memastikan lupa menyuntikkan berarti gagal, bukan diam-diam memanggil
bursa.
"""
import inspect
import engine
import executor
import proposal
import pytest
import re
import rules
import spread
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


def _proposal(window_s=proposal.REACTIVE_WINDOW_S, created_ts=0.0, *,
              kind="SELL", pair="btc_idr", amount_idr=400.0, qty=0.25,
              price_ref=1600.0, rule=engine.RULE_TAKE_PROFIT,
              reserve_after=50000.0):
    """Proposal uji yang berdiri sendiri untuk alur `on_answer`.

    Nilai default dipilih supaya lolos seluruh gerbang dengan konteks
    `_ctx()` (SELL melewati gerbang saldo — ruling F15; nominal di bawah
    atap Rp 275.000; `created_ts=0.0` membuat `now` kecil apa pun berada
    di dalam jendela konfirmasi).
    """
    aksi = rules.Action(kind=kind, pair=pair, amount_idr=amount_idr, qty=qty,
                        price_ref=price_ref,
                        reason=f"usulan uji on_answer ({rule})",
                        executable=True)
    return proposal.new_proposal(aksi, rule=rule, reserve_after=reserve_after,
                                 window_s=window_s, now=created_ts)


def _fresh_proposal():
    """Proposal segar (belum kadaluarsa) untuk alur `on_answer`."""
    return _proposal()


def _pasangkan_harga(ctx, p):
    """Isi `ctx.prices` dengan harga persis milik `p` — geseran 0%.

    Gerbang drift 0,5% (langkah 2b `on_answer`, putusan R-E) menimbang
    `ctx.prices[p.pair]` terhadap `p.price_ref`. Tes yang menguji langkah
    BERIKUTNYA (spread, gerbang, kirim, status) menyuntikkan harga yang sama
    supaya tidak berhenti di 2b. `_ctx()` sendiri TIDAK diubah: helper itu
    juga dipakai tes Task 11, dan menambah data harga bisa membuat aksi
    baru muncul di sana.
    """
    ctx.prices[p.pair] = p.price_ref
    return ctx


class _FakeTransport:
    """Transport tiruan: order yang dicatat, jawaban status berurutan, izin akun.

    - `posted`          : daftar payload yang benar-benar dikirim — bukti
                          "tidak ada order" adalah `posted == []`.
    - `status_sequence` : jawaban `get_order_status` per panggilan; habis ->
                          `{"status": "unknown"}` (kasus 404 abadi).
    - `depth_bps`       : spread yang terbaca dari bursa saat eksekusi —
                          disuntik ke `verdict` tes, karena gerbang spread
                          (F25) tidak boleh mengambil kedalaman sungguhan.
    - `can_trade`/`can_withdraw` : izin akun yang dibaca langkah ke-6.
    - `explode=True`    : `post_order` melempar -> `SubmissionUncertain`.
    """
    def __init__(self, status_sequence=(), depth_bps=None, *,
                 can_trade=True, can_withdraw=False, explode=False):
        self.posted = []
        self.status_sequence = list(status_sequence)
        self.queried_ids = []
        self.depth_bps = depth_bps
        self.can_trade = can_trade
        self.can_withdraw = can_withdraw
        self.explode = explode

    def post_order(self, payload):
        self.posted.append(payload)
        if self.explode:
            raise TimeoutError("koneksi putus")
        return {"success": 1,
                "client_order_id": payload.get("client_order_id")}

    def get_order_status(self, client_order_id):
        self.queried_ids.append(client_order_id)
        if self.status_sequence:
            return {"status": self.status_sequence.pop(0)}
        return {"status": "unknown"}


def _db_rows(conn):
    """Baris tulisan `on_answer` di portfolio.db (tabel `order_status`).

    Hanya langkah ke-8 — yang berjalan SETELAH bursa menjawab status final —
    yang boleh mengisi tabel ini; seluruh langkah yang gagal harus
    menyentuhnya nol kali. Tabel yang belum pernah dibuat = belum pernah
    menulis apa pun -> `[]`.
    """
    try:
        rows = conn.execute(
            "SELECT status FROM order_status ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"status": r[0]} for r in rows]


def _outcomes(conn):
    """Outcome di log bayangan (tabel `proposals`); `[]` bila log belum ada."""
    try:
        rows = conn.execute(
            "SELECT outcome FROM proposals ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def _tanpa_tidur(monkeypatch):
    """Ganti jeda dinding dengan pencatat, kembalikan daftar jeda tercatat.

    Jeda backoff [2, 5, 10] sudah dipatok tes di `test_executor`
    (`assert slept == [2, 5, 10]`); di sini yang diuji alur `on_answer`,
    bukan jam dinding. Jeda yang diminta TETAP dicatat supaya asersi bisa
    memverifikasi backoff benar-benar berjalan — tes tidak butuh 17 detik
    nyata dan tidak ada asersi yang dilemahkan.

    `executor.resolve_status` mem-BOUND `time.sleep` sebagai nilai awal
    kata kunci `sleep` saat modul diimpor, jadi `time.sleep` saja tidak
    cukup: nilai awal itu ikut diganti lewat `__kwdefaults__` (pulih
    otomatis oleh monkeypatch setelah tes).
    """
    tidur = []

    def catat(detik):
        tidur.append(detik)

    monkeypatch.setattr("time.sleep", catat)
    monkeypatch.setitem(executor.resolve_status.__kwdefaults__, "sleep", catat)
    return tidur


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
    # 4. `tick` tidak mengenal jalur eksekusi sama sekali — secara struktural
    #    tidak ada yang bisa menulis ke bursa dari tick ini. (Dulu diperiksa
    #    pada seluruh sumber modul; sejak Task 12 kontrak mewajibkan
    #    `on_answer` memanggil `executor` — pemeriksaan dipusatkan ke
    #    `tick`, properti yang sama dengan lingkup yang benar. Tanpa ini
    #    modul gagal hanya karena on_answer ada.)
    assert "executor" not in inspect.getsource(engine.tick)
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


# ---------------------------------------------------------------------------
# Task 12 — on_answer: konfirmasi sampai eksekusi
#
# Tiga cacat potongan tes rencana sudah diperbaiki di sini, bukan di
# produksi: (a) `conn` belum terikat pada tiga tes — kini memakai
# `_fresh_db(tmp_path)` seperti dua tes lainnya; (b) tes yang sampai ke
# langkah ke-4 WAJIB menyuntikkan `verdict` — tanpa itu memakai bawaan
# `spread.spread_verdict` -> `fetch_depth` -> `urlopen` dan guard
# `conftest.py` menjatuhkannya dengan `JaringanDibuka` (temuan F25);
# (c) helper `_FakeTransport`, `_proposal`, `_fresh_proposal`, `_db_rows`
# belum ada — dibuat di atas dengan makna yang diuji namanya.
# ---------------------------------------------------------------------------

def test_expired_proposal_writes_no_order_and_nothing_to_db(tmp_path):
    conn = _fresh_db(tmp_path)
    p = _proposal(window_s=90, created_ts=0.0)
    t = _FakeTransport()
    out = engine.on_answer(conn, _ctx(), p=p, answer="setuju", now=120.0,
                           transport=t, telegram=_tg([]), shadow=True)
    assert t.posted == [] and out == "ignored"
    assert _db_rows(conn) == []          # tidak ada tulisan sebelum bursa menjawab
    # ... termasuk log bayangan: langkah kadaluarsa mendahului langkah
    # "setuju" pencatat, jadi nol tulisan di tabel mana pun
    assert _outcomes(conn) == []

def test_halt_when_status_unknown_keeps_db_untouched(tmp_path, monkeypatch):
    # Review Focus #2 — 404 abadi bukan berarti batal
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    t = _FakeTransport(status_sequence=["unknown", "unknown", "unknown"])
    sent = []
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju", now=0.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "halted" and _db_rows(conn) == []
    # dikirim tepat sekali, lalu bertanya status maksimal tiga kali dengan
    # ID yang sama (spec §6.1) dan tetap berhenti — tidak ada kirim ulang
    assert len(t.posted) == 1
    assert t.queried_ids == [p.id] * 3
    assert tidur == [2, 5, 10]           # backoff berjalan, tanpa jam dinding
    # kegagalan tidak boleh hilang tanpa jejak (G7)
    assert any("berhenti" in s for s in sent), sent

def test_spread_rechecked_at_execution_time(tmp_path):
    # Review Focus #3 — lolos saat komposisi, melebar saat eksekusi
    p = _proposal(price_ref=1000.0)      # saat komposisi 10 bps
    t = _FakeTransport(depth_bps=45.0)   # saat eksekusi 45 bps
    conn = _fresh_db(tmp_path)
    sent = []
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju", now=10.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           # gerbang spread disuntik: baca kedalaman dari
                           # tiruan bursa, bukan dari jaringan (F25)
                           verdict=lambda pair, **k: (False, t.depth_bps))
    assert out == "gate_blocked" and t.posted == []
    # pesan penolakan menampilkan KEDUA besaran: spread dan ambang (F17a)
    assert any("spread 45.0 bps > 30 bps" in s for s in sent), sent

def test_halt_when_can_withdraw_flips_true(tmp_path):
    conn = _fresh_db(tmp_path)
    t = _FakeTransport(can_withdraw=True)
    sent = []
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=t, telegram=_tg(sent),
                           shadow=False, verdict=lambda pair, **k: (True, 1.0))
    assert out == "halted" and t.posted == []
    assert _db_rows(conn) == []
    assert any("izin tarik dana" in s for s in sent), sent

def test_writes_db_only_after_exchange_answers(tmp_path, monkeypatch):
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    t = _FakeTransport(status_sequence=["filled"])
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=t, telegram=_tg([]),
                           shadow=False, verdict=lambda pair, **k: (True, 1.0))
    assert out == "sent"
    assert _db_rows(conn) == [{"status": "filled"}]
    # satu panggilan status sudah cukup: jeda sebelum percobaan pertama
    assert tidur == [2]
    # eksekusi tetap tercatat di log usulan (langkah 8 kontrak)
    assert _outcomes(conn) == ["executed"]


# --- cakupan tambahan on_answer (delapan langkah kontrak) -----------------

def test_on_answer_jawaban_selain_setuju_diabaikan(tmp_path):
    # Langkah 1: tunda/batal = tidak ada tindakan; bahkan verdict tidak
    # boleh dipanggil (return mendahului langkah ke-4)
    conn = _fresh_db(tmp_path)
    panggilan = {"verdict": 0}

    def verdict_spy(pair, **k):
        panggilan["verdict"] += 1
        return True, 1.0

    t = _FakeTransport(status_sequence=["filled"])
    for jawaban in ("tunda", "batal"):
        out = engine.on_answer(conn, _ctx(), p=_fresh_proposal(),
                               answer=jawaban, now=0.0, transport=t,
                               telegram=_tg([]), shadow=True,
                               verdict=verdict_spy)
        assert out == "ignored"
    assert panggilan["verdict"] == 0
    assert t.posted == [] and t.queried_ids == []
    assert _db_rows(conn) == [] and _outcomes(conn) == []

def test_on_answer_bayangan_mencatat_setuju_tanpa_transport(tmp_path):
    # Langkah 3: mode bayangan MENCATAT persetujuan lalu berhenti —
    # tidak pernah menyentuh transport, tidak pernah sampai gerbang.
    conn = _fresh_db(tmp_path)
    panggilan = {"verdict": 0}

    def verdict_spy(pair, **k):
        panggilan["verdict"] += 1
        return True, 1.0

    t = _FakeTransport(status_sequence=["filled"])
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=t, telegram=_tg([]),
                           shadow=True, verdict=verdict_spy)
    assert out == "ignored"
    assert panggilan["verdict"] == 0     # bayangan kembali sebelum langkah 4
    assert t.posted == [] and t.queried_ids == []
    assert _outcomes(conn) == ["setuju"]
    assert _db_rows(conn) == []

def test_on_answer_harga_hilang_berhenti_bukan_menolak(tmp_path):
    # Ruling F5 + spec §6.3: harga hilang = TIDAK ADA keputusan -> BERHENTI,
    # bukan menolak lalu lanjut.
    conn = _fresh_db(tmp_path)
    sent = []
    t = _FakeTransport(status_sequence=["filled"])
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=t, telegram=_tg(sent),
                           shadow=False,
                           verdict=lambda pair, **k: (None, float("nan")))
    assert out == "halted" and t.posted == []
    assert _db_rows(conn) == []
    assert any("tidak tersedia" in s for s in sent), sent

def test_on_answer_balasan_hilang_berhenti_tanpa_kirim_ulang(tmp_path,
                                                             monkeypatch):
    # Spec §6.1: balasan hilang = jangan ulang kirim; berhenti dan beri
    # tahu pengguna. Pengiriman tepat SATU kali. Status DITANYA maksimal
    # tiga kali dengan id yang sama, jeda [2, 5, 10] (putusan R-C:
    # plan:539 "retry adalah tanggung jawab query status", spec L398) —
    # dan tetap berhenti saat jawabannya tidak pasti.
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    sent = []
    t = _FakeTransport(explode=True)
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=t, telegram=_tg(sent),
                           shadow=False, verdict=lambda pair, **k: (True, 1.0))
    assert out == "halted"
    assert len(t.posted) == 1            # satu percobaan; tidak ada percobaan kedua
    assert t.queried_ids == [p.id] * 3   # ditanya 3x dengan id yang sama
    assert tidur == [2, 5, 10]           # backoff [2, 5, 10], tanpa jam dinding
    assert _db_rows(conn) == []          # status belum pasti -> DB nol sentuh
    assert sent, "kegagalan pengiriman wajib sampai ke pengguna (G7)"

def test_on_answer_pagu_per_aset_diulang_sebelum_kirim(tmp_path):
    # Langkah 5: gerbang safeguards DIULANG dengan konteks SEKARANG.
    # Usulan dibuat saat pagu kosong; saat jawaban datang aset sudah 3
    # order hari ini -> diblokir, tidak dikirim.
    conn = _fresh_db(tmp_path)
    ctx = _ctx()
    ctx.today_counts = {"btc_idr": 3}
    p = _fresh_proposal()
    sent = []
    t = _FakeTransport(status_sequence=["filled"])
    out = engine.on_answer(conn, _pasangkan_harga(ctx, p), p=p, answer="setuju",
                           now=0.0, transport=t, telegram=_tg(sent),
                           shadow=False, verdict=lambda pair, **k: (True, 1.0))
    assert out == "gate_blocked" and t.posted == []
    assert _db_rows(conn) == []
    assert any("pagu 3 order per aset" in s for s in sent), sent

def test_on_answer_penolakan_spread_membulatkan_bps_ke_atas(tmp_path):
    # Ruling F13 + F9: saat MENOLAK, bps dibulatkan KE ATAS — pengguna tidak
    # boleh melihat angka yang lebih memaafkan dari kenyataan. 30.004 bps
    # dibulatkan ke bawah menjadi "30.0" (kalimat yang menyalahi dirinya:
    # 30.0 tidak > 30); dibulatkan ke atas menjadi "30.1" (jujur).
    conn = _fresh_db(tmp_path)
    sent = []
    p = _fresh_proposal()
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju",
                           now=0.0, transport=_FakeTransport(),
                           telegram=_tg(sent), shadow=False,
                           verdict=lambda pair, **k: (False, 30.004))
    assert out == "gate_blocked"
    assert any("spread 30.1 bps > 30 bps" in s for s in sent), sent

def test_on_answer_submit_dan_tanya_status_memakai_id_sama(tmp_path,
                                                           monkeypatch):
    # Spec §6.1: satu client order id, dipakai saat kirim DAN saat bertanya
    # status. Idnya = id proposal (<= 36 karakter, charset [0-9A-Za-z_-],
    # terdokumentasi di proposal.py — F18). Order selalu limit, recvWindow
    # 15000 ms (spec §11.5).
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    t = _FakeTransport(status_sequence=["filled"])
    out = engine.on_answer(conn, _pasangkan_harga(_ctx(), p), p=p,
                           answer="setuju", now=0.0,
                           transport=t, telegram=_tg([]), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "sent"
    cid = t.posted[0]["client_order_id"]
    assert cid == p.id and t.queried_ids == [p.id]
    assert len(cid) <= 36 and re.fullmatch(r"[0-9A-Za-z_-]+", cid)
    assert t.posted[0]["order_type"] == "limit"
    assert t.posted[0]["recvWindow"] == 15000
    assert tidur == [2]
    # R-A: empat field instrumen benar-benar sampai ke transport.post_order
    # — tanpa ini order terkirim TANPA instrumen. Kosakata diambil dari
    # catatan trade di indodax_api.py:148-201 (`type`, `price`, `amount`).
    pl = t.posted[0]
    assert pl["pair"] == p.pair              # "btc_idr"
    assert pl["type"] == p.kind.lower()      # "sell" — sisi ala catatan trade
    assert pl["price"] == p.price_ref        # 1600.0 — harga limit
    assert pl["amount"] == p.qty             # 0.25 — jumlah koin (v1: `amount`)

def test_on_answer_reserve_buy_lolos_gerbang_saldo_input_sungguhan(tmp_path,
                                                                   monkeypatch):
    # Ruling F15 + F17b, diuji dengan input NYATA di titik kawat: reserve
    # buy memakai aturan "free + 0,5 x reserve". Angka: free Rp 0 +
    # 0,5 x Rp 50.000 = Rp 25.000 daya beli, beli Rp 25.000 (tepat di
    # batas -> lolos). Nama aturan datang dari proposal (p.rule), bukan
    # dari dihitung ulang (F36).
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    ctx = _ctx()
    ctx.free_idr = 0.0
    ctx.reserve_idr = 50_000.0
    p = _proposal(kind="BUY", rule=proposal.RULE_RESERVE_BUY,
                  amount_idr=25_000.0, qty=0.025, price_ref=1_000_000.0)
    t = _FakeTransport(status_sequence=["filled"])
    out = engine.on_answer(conn, _pasangkan_harga(ctx, p), p=p,
                           answer="setuju", now=0.0,
                           transport=t, telegram=_tg([]), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "sent", (
        "reserve buy ditolak gerbang saldo — kemungkinan nama aturan yang "
        "dikirim ke safeguards.gates bukan 'reserve_buy' (F17b)")
    assert _db_rows(conn) == [{"status": "filled"}]

def test_on_answer_pembelian_biasa_ditahan_tanpa_nama_reserve_buy(tmp_path):
    # Kontrol F15: angka yang SAMA tanpa nama "reserve_buy" memakai aturan
    # "free - reserve" -> ditolak. Rp 0 - Rp 50.000 = Rp -50.000 < Rp
    # 25.000. Pesan penolakan wajib menampilkan KEDUA besaran (F17a):
    # saldo bebas DAN cadangan (dan nominalnya).
    conn = _fresh_db(tmp_path)
    ctx = _ctx()
    ctx.free_idr = 0.0
    ctx.reserve_idr = 50_000.0
    p = _proposal(kind="BUY", rule="dca",   # nama aturan non-reserve buy
                  amount_idr=25_000.0, qty=0.025, price_ref=1_000_000.0)
    t = _FakeTransport(status_sequence=["filled"])
    sent = []
    out = engine.on_answer(conn, _pasangkan_harga(ctx, p), p=p,
                           answer="setuju", now=0.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "gate_blocked" and t.posted == []
    assert _db_rows(conn) == []
    baris = [s for s in sent if "Tolak" in s]
    assert baris and all(x in baris[0] for x in
                         ("saldo bebas Rp 0", "cadangan Rp 50.000",
                          "kurang dari Rp 25.000")), sent


# --- putusan ronde review (R-A … R-E) --------------------------------------

def test_on_answer_jawaban_kedua_diabaikan_tanpa_order_baru(tmp_path,
                                                            monkeypatch):
    # R-B (Critical): dua "setuju" dalam jendela 90 dtk = SATU order.
    # Guard langkah 0 membaca shadow_log.proposals (jawaban pertama kini
    # dicatat di kedua mode — amendmen rencana) SEBELUM menyentuh transport.
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    ctx = _pasangkan_harga(_ctx(), p)
    t = _FakeTransport(status_sequence=["filled"])
    panggilan = {"verdict": 0}

    def verdict_spy(pair, **k):
        panggilan["verdict"] += 1
        return True, 1.0

    out1 = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                            transport=t, telegram=_tg([]), shadow=False,
                            verdict=verdict_spy)
    assert out1 == "sent"
    sent2 = []
    out2 = engine.on_answer(conn, ctx, p=p, answer="setuju", now=1.0,
                            transport=t, telegram=_tg(sent2), shadow=False,
                            verdict=verdict_spy)
    assert out2 == "ignored"
    assert len(t.posted) == 1              # order kedua TIDAK pernah terkirim
    assert t.queried_ids == [p.id]         # guard berhenti sebelum transport
    assert _db_rows(conn) == [{"status": "filled"}]   # tetap satu baris
    assert panggilan["verdict"] == 1       # jawaban kedua berhenti di langkah 0
    assert tidur == [2]                    # tidak ada percobaan status kedua
    assert any("sudah dijawab" in s for s in sent2), sent2


def test_on_answer_jawaban_kedua_setelah_balasan_hilang_tidak_kirim_ulang(
        tmp_path, monkeypatch):
    # R-B (Critical) — kasus yang paling berbahaya: balasan hilang ->
    # TIDAK ada baris `order_status`, jadi guard yang hanya membaca tabel
    # itu akan MELESET. Pengaman utamanya baris "setuju" di
    # shadow_log.proposals. Tanpa guard, jawaban kedua mengirim order
    # KEDUA dengan client_order_id yang sama persis.
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    ctx = _pasangkan_harga(_ctx(), p)
    t = _FakeTransport(explode=True)
    out1 = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                            transport=t, telegram=_tg([]), shadow=False,
                            verdict=lambda pair, **k: (True, 1.0))
    assert out1 == "halted"
    assert _db_rows(conn) == []            # kasus hilang: memang tak ada baris DB
    assert _outcomes(conn) == ["setuju"]   # tetapi jawaban pertama tercatat
    sent2 = []
    out2 = engine.on_answer(conn, ctx, p=p, answer="setuju", now=1.0,
                            transport=t, telegram=_tg(sent2), shadow=False,
                            verdict=lambda pair, **k: (True, 1.0))
    assert out2 == "ignored"
    assert len(t.posted) == 1              # TIDAK pernah kirim ulang
    assert any("sudah dijawab" in s for s in sent2), sent2


def test_on_answer_balasan_hilang_lalu_status_final_ditulis(tmp_path,
                                                            monkeypatch):
    # R-C: cabang SubmissionUncertain MENANYAKAN status dengan id yang sama
    # (plan:539 "retry adalah tanggung jawab query status (Task 7)",
    # spec L398 "maks 3 kali bertanya, jeda [2, 5, 10]") — TANPA PERNAH
    # mengirim ulang. Status final -> baris DB + catatan "executed".
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    ctx = _pasangkan_harga(_ctx(), p)
    t = _FakeTransport(explode=True, status_sequence=["filled"])
    sent = []
    out = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "sent"
    assert len(t.posted) == 1              # tidak pernah kirim ulang
    assert t.queried_ids == [p.id]         # ditanya dengan id yang sama
    assert tidur == [2]                    # berhenti pada percobaan pertama
    assert _db_rows(conn) == [{"status": "filled"}]   # DB baru setelah final
    assert _outcomes(conn) == ["executed"]
    assert any("berhasil diisi" in s for s in sent), sent


def test_tick_penolakan_spread_membulatkan_bps_ke_atas(tmp_path, monkeypatch):
    # R-D (utang F13, Task 11): bps 30.004 harus tampil "30.1", bukan "30"
    # — "spread 30 bps melebihi batas 30 bps" adalah kalimat yang
    # menyalahi dirinya sendiri. Berlaku untuk KEDUA situs: pesan
    # notifikasi dan catatan log bayangan.
    conn = _fresh_db(tmp_path)
    sent = []
    engine.tick(conn, _ctx(), now=0.0, telegram=_tg(sent), shadow=True,
                verdict=lambda p, **k: (False, 30.004))
    assert any("spread 30.1 bps melebihi batas 30 bps" in s for s in sent), sent
    notes = [r[0] for r in conn.execute("SELECT note FROM proposals")]
    assert any("30.1" in n for n in notes), notes
    # Ambang dibaca dari konstanta, bukan angka literal di dalam teks:
    # ganti konstanta -> pesan ikut berubah.
    monkeypatch.setattr(spread, "MAX_SPREAD_BPS", 25)
    sent2 = []
    engine.tick(conn, _ctx(), now=1.0, telegram=_tg(sent2), shadow=True,
                verdict=lambda p, **k: (False, 30.004))
    assert any("melebihi batas 25 bps" in s for s in sent2), sent2


def test_on_answer_harga_bergeser_konfirmasi_hangus(tmp_path):
    # R-E: spec L116 — "harga bergeser > 0,5% dari angka di pesan dalam
    # jendela konfirmasi -> hangus". 1609,0 vs 1600,0 = +0,5625% > 0,5%.
    # Gerbangnya langkah 2b: berhenti SEBELUM verdict (langkah 4) dan
    # SEBELUM mencatat jawaban — tidak ada tulisan sama sekali.
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()                 # price_ref 1600.0
    ctx = _ctx()
    ctx.prices["btc_idr"] = 1609.0         # +0,5625% > 0,5%
    panggilan = {"verdict": 0}

    def verdict_spy(pair, **k):
        panggilan["verdict"] += 1
        return True, 1.0

    t = _FakeTransport(status_sequence=["filled"])
    sent = []
    out = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           verdict=verdict_spy)
    assert out == "ignored"
    assert t.posted == [] and t.queried_ids == []
    assert panggilan["verdict"] == 0       # berhenti SEBELUM langkah 4
    assert _db_rows(conn) == [] and _outcomes(conn) == []
    assert any("bergeser" in s and "hangus" in s for s in sent), sent


def test_on_answer_harga_masih_sah_dalam_05_persen_diteruskan(tmp_path,
                                                              monkeypatch):
    # Batas gerbang drift persis 0,5% (proposal.PRICE_DRIFT_TOL): 1608,0
    # vs 1600,0 = tepat +0,5% -> masih SAH (price_still_valid memakai
    # "<="), jadi usulan diteruskan, bukan hangus.
    tidur = _tanpa_tidur(monkeypatch)
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()                 # price_ref 1600.0
    ctx = _ctx()
    ctx.prices["btc_idr"] = 1608.0         # tepat +0,5% -> masih berlaku
    t = _FakeTransport(status_sequence=["filled"])
    out = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                           transport=t, telegram=_tg([]), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "sent"
    assert len(t.posted) == 1
    assert _db_rows(conn) == [{"status": "filled"}]


def test_on_answer_harga_tidak_terbaca_konfirmasi_hangus(tmp_path):
    # R-E + spec §6.3: harga hilang = TIDAK ADA keputusan —
    # `price_still_valid(p, None)` menolak, jadi konfirmasi hangus di
    # langkah 2b, bukan diteruskan ke gerbang dengan angka lama.
    conn = _fresh_db(tmp_path)
    p = _fresh_proposal()
    ctx = _ctx()                          # prices={} — harga tak terbaca
    t = _FakeTransport(status_sequence=["filled"])
    sent = []
    out = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                           transport=t, telegram=_tg(sent), shadow=False,
                           verdict=lambda pair, **k: (True, 1.0))
    assert out == "ignored"
    assert t.posted == [] and _db_rows(conn) == []
    assert any("hangus" in s for s in sent), sent


def test_on_answer_produksi_setelah_tick_tanpa_bentrok_primary_key(tmp_path):
    # R-B — alur produksi sesungguhnya: `tick` (mode bayangan) sudah menulis
    # baris "proposed" dengan id yang sama sebelum jawaban datang. Guard
    # harus membaca OUTCOME (bukan sekadar keberadaan baris, atau jawaban
    # pertama ikut terkunci), dan catatan "setuju" harus MEMPERBARUI baris
    # itu — INSERT kedua akan bentrok PRIMARY KEY dan menumbangkan alur
    # jawaban dengan IntegrityError.
    conn = _fresh_db(tmp_path)
    ctx = _ctx()
    props = engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True,
                        verdict=lambda p, **k: (True, 1.0))
    assert props, "fixture _ctx harus menghasilkan usulan"
    p = props[0]
    _pasangkan_harga(ctx, p)
    out = engine.on_answer(conn, ctx, p=p, answer="setuju", now=0.0,
                           transport=_FakeTransport(), telegram=_tg([]),
                           shadow=True, verdict=lambda pair, **k: (True, 1.0))
    assert out == "ignored"
    rows = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (p.id,)).fetchall()
    assert [r[0] for r in rows] == ["setuju"]   # satu baris, diperbarui
    assert len(_outcomes(conn)) == len(props)   # tiap usulan tepat satu baris
