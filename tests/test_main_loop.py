"""Tes loop utama, sakelar mode bayangan, dan integrasi seluruh modul.

Tidak ada tes di sini yang boleh membuka jaringan: seluruh jalur keluar
(gerbang spread, Telegram, transport bursa, polling `getUpdates`) disuntikkan
lewat argumen `verdict`, `telegram`, `transport`, dan `fetch`. Guard di
`conftest.py` (root) memastikan lupa menyuntikkan berarti gagal keras, bukan
diam-diam memanggil bursa.

Fokus tes ini pada perkara yang bisa gagal diam-diam:
- pencacah `shadow_day` **maksimal sekali per hari lokal** (kalau tidak,
  `v1_enabled` menyala setelah ~3 jam, bukan 7 hari);
- satu penolakan selalu mereset, meski hari itu sudah dihitung;
- jalur fisik ke bursa benar-benar tidak tersentuh dalam mode bayangan
  (diuji dengan transport perekam, bukan daftar kosong yang tak pernah diisi);
- rekonsiliasi berhenti **sebelum** ada usulan apa pun;
- polling Telegram benar-benar membaca jawaban dan `/ulang`.
"""
import inspect
import os
import sqlite3
import time

import pytest

import dca_window
import engine
import main_loop
import proposal
import rules
import shadow_log
from portfolio import Holding


# --------------------------------------------------------------------------
# Pembantu
# --------------------------------------------------------------------------

def _ts(hour=12, day=10, minute=0):
    """Timestamp epoch dari WAKTU LOKAL (pola tests/test_dca_window.py).

    Tanggal memakai hari tetap (1-28) supaya tidak pernah menabrak panjang
    bulan yang berbeda, dan supaya "hari berikutnya" benar-benar hari
    berikutnya pada bulan yang sama.
    """
    kini = time.localtime()
    return time.mktime(
        time.struct_time((kini.tm_year, kini.tm_mon, day, hour, minute, 0, 0, 0, -1))
    )


_URUTAN_DB = [0]


def _fresh_db(tmp_path):
    """Koneksi SQLite baru dan kosong — tabel TIDAK dibuat di sini.

    Pembuat tabel milik `dca_window.ensure_bot_state` (R-G); kalau helper ini
    membuatnya, pengujian "satu pembuat tabel" jadi tidak berarti.

    Nama berkas unik per pemanggilan: dua koneksi ke berkas yang sama akan
    berbagi `proposals`, dan `id` usulan yang kembar (waktu tick identik)
    akan menabrak PRIMARY KEY — kegagalan palsu yang menyamar sebagai cacat
    produksi.
    """
    _URUTAN_DB[0] += 1
    conn = sqlite3.connect(os.path.join(str(tmp_path), f"test{_URUTAN_DB[0]}.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _nama_tabel(conn):
    return {
        baris[0]
        for baris in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _kolom(conn, tabel):
    return [baris[1] for baris in conn.execute(f"PRAGMA table_info({tabel})")]


def _act(kind="SELL", pair="btc_idr", amount_idr=28000.0, qty=20.0,
         price_ref=1400.0):
    """Satu `rules.Action` yang sah sebagai masukan `proposal.new_proposal`."""
    return rules.Action(
        kind=kind,
        pair=pair,
        amount_idr=amount_idr,
        qty=qty,
        price_ref=price_ref,
        reason="Untung bersih 132,9% dari harga rata-rata (level +100%)",
        executable=True,
    )


def _ctx(price=1400.0):
    """Konteks engine dengan SATU aksi take-profit yang lolos semua gerbang.

    `prices` sengaja diisi: `engine.on_answer` membacanya untuk gerbang geser
    harga 0,5% (kabel wajib Task 12) — `prices={}` membuat setiap konfirmasi
    berbalik "ignored" tanpa satu pun alasan yang jujur.
    """
    holdings = [
        Holding(
            pair="btc_idr", coin="btc", qty=40.0,
            avg_cost=600.0, price=price,
            value_idr=40.0 * price, cost_basis_idr=40.0 * 600.0,
            unrealized_idr=40.0 * (price - 600.0), basis_known=True,
        )
    ]
    return engine.Context(
        holdings=holdings,
        prices={"btc_idr": price},
        free_idr=100000.0,
        reserve_idr=50000.0,
        today_counts={},
        last_fill_ts={},
        held_pairs_24h=set(),
    )


def _ctx_kosong():
    """Konteks tanpa posisi: `engine.tick` tidak menghasilkan apa pun."""
    return engine.Context(
        holdings=[], prices={}, free_idr=0.0, reserve_idr=0.0,
        today_counts={}, last_fill_ts={}, held_pairs_24h=set(),
    )


# Harga jujur ketiga pasangan target DCA — angka bulat wajar, pola yang sama
# dengan tests/test_engine_dca.py (HARGA).
HARGA_DCA = {"btc_idr": 1600.0, "eth_idr": 1400.0, "sol_idr": 350.0}


def _ctx_dca(free_idr=600000.0):
    """Konteks DCA: tanpa posisi, saldo bebas cukup untuk ketiga pasangan
    target (btc Rp 275.000 terpotong atap per order, eth Rp 138.000,
    sol Rp 55.000 — diverifikasi terhadap `rules.dca_actions` di
    tests/test_engine_dca.py).

    `dca_enabled=True` bawaan hanya dipakai jalur `apply_ulang` langsung;
    `tick_cycle` menghitung ulang sakelar per siklus dari `boleh_usul_dca`.
    """
    return engine.Context(
        holdings=[], prices=dict(HARGA_DCA), free_idr=free_idr,
        reserve_idr=0.0, today_counts={}, last_fill_ts={},
        held_pairs_24h=set(), dca_enabled=True,
    )


def _usulan_dca(now, pair="btc_idr", price=1400.0, amount_idr=100000.0):
    """Satu usulan DCA SUNGGUHAN: rule `dca_bulanan` + jendela 24 jam.

    Dipakai tes tombol (F46): id tak dikenal tidak boleh menyentuh
    jendela, jadi tombol ditekan pada proposal yang benar-benar terdaftar.
    """
    aksi = rules.Action(
        kind="BUY", pair=pair, amount_idr=amount_idr,
        qty=amount_idr / price, price_ref=price,
        reason="DCA bulanan: beli sesuai bobot target", executable=True,
    )
    return proposal.new_proposal(
        aksi, rule=rules.RULE_DCA, reserve_after=0.0,
        window_s=proposal.DCA_WINDOW_S, now=now,
    )


def _usulan_take_profit(now):
    """Satu usulan take-profit sungguhan (rule reaktif, jendela 90 detik)."""
    return proposal.new_proposal(
        _act(), rule="take_profit", reserve_after=0.0,
        window_s=proposal.REACTIVE_WINDOW_S, now=now,
    )


class _TelegramPerekam:
    """Telegram tiruan yang mencatat pesan dan konfirmasi yang dikirim."""

    def __init__(self, token="token-uji"):
        self.token = token
        self.lines = []
        self.konfirmasi = []

    def line(self, text):
        self.lines.append(text)
        return True

    def confirmation(self, p):
        self.konfirmasi.append(p)
        return 4242  # message_id salah satu; None berarti "gagal kirim"


class _TransportPerekam:
    """Transport tiruan yang mencatat SETIAPA order dan pertanyaan status."""

    def __init__(self, *, can_trade=True, can_withdraw=False,
                 status="FILLED"):
        self.posted = []
        self.queried = []
        self.can_trade = can_trade
        self.can_withdraw = can_withdraw
        self.status = status

    def post_order(self, payload):
        self.posted.append(payload)
        return {"status": self.status, "order_id": 1}

    def get_order_status(self, client_order_id):
        self.queried.append(client_order_id)
        return {"status": self.status}


class _FetchPerekam:
    """`fetch` tiruan untuk `telegram_gate.get_updates` (tanpa jaringan)."""

    def __init__(self, updates):
        self.updates = list(updates)
        self.panggilan = []

    def __call__(self, token, *, offset, timeout):
        self.panggilan.append((token, offset, timeout))
        return {"result": list(self.updates)}


def _update_tombol(aksi, id_usulan):
    return {"update_id": 1, "callback_query": {"data": f"{aksi}:{id_usulan}"}}


def _update_teks(teks):
    return {"update_id": 2, "message": {"text": teks}}


def _ok_verdict(pair, **kwargs):
    """Gerbang spread yang selalu lolos — tanpa jaringan, tanpa float mustahil."""
    return (True, 1.0)


def _satu_siklus(conn, **kwargs):
    """Panggilan `tick_cycle` dengan nilai baku yang tidak diuji di sini."""
    # `ctx` sengaja dibuat saat dipanggil, bukan sebagai nilai bawaan yang
    # dievaluasi sekali: `Context` memuat dict/set yang bisa berubah
    # (last_fill_ts, held_pairs_24h), jadi satu objek yang dibagi ke semua
    # tes akan membuat hasil tes bergantung pada urutan tes.
    akses = dict(
        now=_ts(hour=12),
        telegram=_TelegramPerekam(),
        transport=None,
        shadow=True,
        ctx=None,
        verdict=_ok_verdict,
    )
    akses.update(kwargs)
    if akses["ctx"] is None:
        akses["ctx"] = _ctx()
    return main_loop.tick_cycle(conn, **akses)


@pytest.fixture(autouse=True)
def _bersihkan_state():
    """Setiap tes mulai dari daftar usulan & slot pengingat yang kosong."""
    main_loop.reset_state()
    yield
    main_loop.reset_state()


# --------------------------------------------------------------------------
# should_run_habitual — DND satu sumber kebenaran (R-4)
# --------------------------------------------------------------------------

def test_habitual_skips_dnd_hours():
    assert main_loop.should_run_habitual(_ts(hour=3)) is False
    assert main_loop.should_run_habitual(_ts(hour=12)) is True


def test_habitual_batas_jam_dnd_tepat():
    """23.00 masuk DND, 07.00 keluar — batas awal inklusif, akhir eksklusif."""
    assert main_loop.should_run_habitual(_ts(hour=23, minute=0)) is False
    assert main_loop.should_run_habitual(_ts(hour=22, minute=59)) is True
    assert main_loop.should_run_habitual(_ts(hour=7, minute=0)) is True
    assert main_loop.should_run_habitual(_ts(hour=6, minute=59)) is False


def test_habitual_mendelegasikan_ke_dnd_dca_window(monkeypatch):
    """`should_run_habitual` harus DELEGASI, bukan menulis ulang jam DND.

    Pin non-vakum: kalau `dca_window.in_dnd` diganti, hasilnya harus ikut —
    implementasi yang menyalin `ACTIVE_HOURS` sendiri tidak akan terpengaruh.
    """
    monkeypatch.setattr(dca_window, "in_dnd", lambda ts: True)
    assert main_loop.should_run_habitual(_ts(hour=12)) is False
    monkeypatch.setattr(dca_window, "in_dnd", lambda ts: False)
    assert main_loop.should_run_habitual(_ts(hour=3)) is True


def test_jam_aktif_diturunkan_dari_dnd_bukan_literal_baru():
    """ACTIVE_HOURS harus turunan DND — bukan daftar jam kedua yang bisa melenceng."""
    assert main_loop.ACTIVE_HOURS == (7, 23)
    assert main_loop.ACTIVE_HOURS == (dca_window.DND[1], dca_window.DND[0])


# --------------------------------------------------------------------------
# Sakelar mode bayangan (R-1, R-5, R-6)
# --------------------------------------------------------------------------

def test_set_shadow_day_menamai_nilai_absolut_bukan_menambah(tmp_path):
    """Namanya "set": panggilan kedua MENGGANTI, bukan menambah."""
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, 3)
    main_loop.set_shadow_day(conn, 9)
    assert main_loop.load_state(conn)["shadow_day"] == 9


def test_sambutan_batas_hari_naik_kelas(tmp_path):
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, 6)
    assert main_loop.load_state(conn)["v1_enabled"] is False
    main_loop.set_shadow_day(conn, 7)
    assert main_loop.load_state(conn)["v1_enabled"] is True
    # Setelah menyala tidak boleh berkedip mati-mati.
    main_loop.set_shadow_day(conn, 8)
    assert main_loop.load_state(conn)["v1_enabled"] is True


def test_ambang_naik_kelas_adalah_konstanta_yang_dinamai(tmp_path):
    """Spec §9: 7-14 hari; 7 adalah LANTAI. Angka itu tidak boleh inline."""
    assert main_loop.GO_LIVE_SHADOW_DAYS == 7
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, main_loop.GO_LIVE_SHADOW_DAYS - 1)
    assert main_loop.load_state(conn)["v1_enabled"] is False
    main_loop.set_shadow_day(conn, main_loop.GO_LIVE_SHADOW_DAYS)
    assert main_loop.load_state(conn)["v1_enabled"] is True


def test_load_state_membaca_bot_state_yang_sama(tmp_path):
    """`load_state` WAJIB memakai tabel `dca_window`, tidak membuat yang kedua."""
    conn = _fresh_db(tmp_path)
    main_loop.load_state(conn)
    main_loop.set_shadow_day(conn, 4)
    main_loop.apply_shadow_gate(conn, now=_ts(hour=12))
    assert _nama_tabel(conn) == {"bot_state"}
    # Nilai yang ditulis dca_window harus terbaca lewat load_state.
    dca_window.mark_pending(conn, _ts(hour=9))
    assert main_loop.load_state(conn)["last_dca_date"] == time.strftime(
        "%Y-%m", time.localtime(_ts(hour=9))
    )


def test_kolom_penanda_hari_ditambahkan_idempoten():
    """Basis data lama (tanpa kolom baru) diperluas sekali, kolom lain utuh."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE bot_state ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " last_dca_date TEXT,"
        " v1_enabled INTEGER NOT NULL DEFAULT 0,"
        " shadow_day INTEGER NOT NULL DEFAULT 0,"
        " dca_sent_ts REAL,"
        " dca_status TEXT)"
    )
    conn.execute(
        "INSERT INTO bot_state (id, last_dca_date, v1_enabled, shadow_day,"
        " dca_sent_ts, dca_status) VALUES (1, '2026-10', 1, 4, 123.0,"
        " 'confirmed')"
    )
    conn.commit()

    dca_window.ensure_bot_state(conn)
    dca_window.ensure_bot_state(conn)   # harus menjadi no-op

    assert _kolom(conn, "bot_state") == [
        "id", "last_dca_date", "v1_enabled", "shadow_day",
        "dca_sent_ts", "dca_status", "shadow_checked_day",
    ]
    baris = conn.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
    assert baris["shadow_day"] == 4
    assert baris["v1_enabled"] == 1
    assert baris["last_dca_date"] == "2026-10"
    assert baris["dca_sent_ts"] == 123.0
    assert baris["dca_status"] == "confirmed"
    assert baris["shadow_checked_day"] is None


def test_penolakan_mengosongkan_hari_bayan_tanpa_menaikkan(tmp_path):
    """Satu penolakan = reset ke NOL, bukan 9 lalu naik lagi jadi 10.

    `rejected_count` hanya menghitung satu hari lokal, jadi catatan dibuat
    dengan waktu yang SAMA dengan `now` yang diberikan ke gerbang.
    """
    conn = _fresh_db(tmp_path)
    sekarang = _ts(hour=12)
    main_loop.set_shadow_day(conn, 9)
    p = proposal.new_proposal(_act(), rule="take_profit", reserve_after=0.0,
                              window_s=90, now=sekarang)
    shadow_log.record(conn, p, "spread_rejected")

    main_loop.apply_shadow_gate(conn, now=sekarang)

    keadaan = main_loop.load_state(conn)
    assert keadaan["shadow_day"] == 0
    assert keadaan["v1_enabled"] is False


def test_siklus_berulang_dalam_satu_hari_tidak_menaikkan_hari(tmp_path):
    """12 denyut jantung (5 menit) dalam satu hari = SATU hari bayangan.

    Inilah cacat yang paling berbahaya di tugas ini: tanpa penanda hari,
    `v1_enabled` menyala sekitar pukul 15.00 pada hari pertama.
    """
    conn = _fresh_db(tmp_path)
    for menit in range(0, 60, 5):
        main_loop.apply_shadow_gate(conn, now=_ts(hour=9, minute=menit))
    assert main_loop.load_state(conn)["shadow_day"] == 1
    # Denyut terakhir pada hari yang sama juga tidak menambah.
    main_loop.apply_shadow_gate(conn, now=_ts(hour=10, minute=0))
    assert main_loop.load_state(conn)["shadow_day"] == 1


def test_hari_baru_bernilai_satu_kali_naik(tmp_path):
    conn = _fresh_db(tmp_path)
    main_loop.apply_shadow_gate(conn, now=_ts(day=10, hour=9))
    main_loop.apply_shadow_gate(conn, now=_ts(day=10, hour=18))
    main_loop.apply_shadow_gate(conn, now=_ts(day=11, hour=6))
    assert main_loop.load_state(conn)["shadow_day"] == 2


def test_penolakan_setelah_hari_dihitung_tetap_mereset(tmp_path):
    """Penolakan yang muncul SETELAH incremented hari ini tetap mereset.

    Urutan di `apply_shadow_gate`: cek penolakan selalu lebih dulu, penanda
    hari hanya boleh menaikkan angka.
    """
    conn = _fresh_db(tmp_path)
    sekarang = _ts(hour=12)
    main_loop.apply_shadow_gate(conn, now=sekarang)
    assert main_loop.load_state(conn)["shadow_day"] == 1

    p = proposal.new_proposal(_act(), rule="take_profit", reserve_after=0.0,
                              window_s=90, now=sekarang + 60)
    shadow_log.record(conn, p, "gate_rejected")
    main_loop.apply_shadow_gate(conn, now=sekarang + 120)

    keadaan = main_loop.load_state(conn)
    assert keadaan["shadow_day"] == 0
    assert keadaan["v1_enabled"] is False


def test_tujuh_hari_bersih_menyalakan_sakelar(tmp_path):
    """Jalan penuh: 7 hari tanpa penolakan menyalakan sakelar (spec §9)."""
    conn = _fresh_db(tmp_path)
    for hari in range(10, 17):
        main_loop.apply_shadow_gate(conn, now=_ts(day=hari, hour=9))
        main_loop.apply_shadow_gate(conn, now=_ts(day=hari, hour=20))
    keadaan = main_loop.load_state(conn)
    assert keadaan["shadow_day"] == 7
    assert keadaan["v1_enabled"] is True


# --------------------------------------------------------------------------
# tick_cycle — urutan, bayangan, dan jalur bursa
# --------------------------------------------------------------------------

def test_modus_bayangan_tidak_pernah_menyentuh_bursa(tmp_path):
    """Pengganti tes vakum: transport PEREKAM, lalu buktikan tidak terpakai.

    Tes lama (`built == []`) selalu lulus karena tidak ada yang pernah
    mengisi `built`. Di sini transport benar-benar ada, dan `posted`/
    `queried_ids` harus tetap kosong sementara usulan justru TERBENTUK —
    jadi jalur bursa benar-benar berjalan tanpa menyentuh bursa.
    """
    conn = _fresh_db(tmp_path)
    # Mode bayangan memakai kunci View-Only: canTrade False (spec §6.6).
    transport = _TransportPerekam(can_trade=False, can_withdraw=False)
    telegram = _TelegramPerekam()

    keluar = _satu_siklus(conn, telegram=telegram, transport=transport,
                          shadow=True)

    # Bukan vakum: usulan benar-benar terbentuk dan dicatat.
    assert keluar == "proposed"
    assert len(telegram.konfirmasi) == 1
    assert telegram.konfirmasi[0].price_ref == 1400.0
    # Namun tidak ada satu pun order yang dikirim atau ditanyakan.
    assert transport.posted == []
    assert transport.queried == []


def test_setuju_di_bayangan_dengan_transport_terpasang_tidak_mengirim_order(
        tmp_path):
    """Tekan Setuju di mode bayangan walau transport TERPASANG: bursa tetap
    tidak tersentuh.

    Inilah konfigurasi paling berbahaya yang bisa dimiliki bot ini: transport
    ada dan siap mencatat order apa pun yang salah kirim, tapi mode bayangan
    masih menyala. Mutasi yang memaksa `shadow=False` pada `tick_cycle` harus
    gagal DI SINI lewat pernyataan (outcome bukan "setuju"), bukan lewat
    kecelakaan (traceback) — menyusul kelemahan yang ditemukan mutasi M5b di
    ronde sebelumnya, saat deteksi hanya terjadi karena sebuah AttributeError.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    # Kunci View-Only di mode bayangan: canTrade False (spec §6.6).
    transport = _TransportPerekam(can_trade=False, can_withdraw=False)

    # Usulan didaftarkan langsung, bukan lewat siklus pertama: bila mutasi
    # membuat siklus berhenti sebelum membentuk usulan, `telegram.konfirmasi`
    # kosong dan tes gagal dengan IndexError — kecelakaan, bukan pernyataan.
    usulan = proposal.new_proposal(
        _act(), rule="take_profit", reserve_after=0.0, window_s=90,
        now=_ts(hour=12),
    )
    telegram.confirmation(usulan)                    # pesan konfirmasi terkirim
    shadow_log.record(conn, usulan, "proposed", "")  # jejak audit seperti tick
    main_loop._ingat([usulan])                       # menunggu jawaban

    fetch = _FetchPerekam([_update_tombol("setuju", usulan.id)])
    _satu_siklus(conn, telegram=telegram, transport=transport, shadow=True,
                 now=_ts(hour=12, minute=1), ctx=_ctx(), fetch=fetch)

    # Jawaban tetap DIPROSES: tercatat "setuju" di jejak audit.
    baris = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (usulan.id,)
    ).fetchone()
    assert baris[0] == "setuju"
    # Namun bursa tidak PERNAH disentuh: tidak ada order terkirim dan tidak
    # ada pun status yang ditanya.
    assert transport.posted == []
    assert transport.queried == []


def test_selisih_berhenti_sebelum_ada_usulan(tmp_path):
    """Rekonsiliasi menghentikan tick SEBELUM usulan apa pun dibentuk.

    Nama tes lama menjanjikan "before any proposal" tapi hanya memeriksa
    `out == "halted"`. Yang di sini dipin: tidak ada usulan yang dikirim ke
    Telegram, tidak ada baris "proposed", dan tidak ada yang keluar ke bursa.
    """
    conn = _fresh_db(tmp_path)
    transport = _TransportPerekam()
    telegram = _TelegramPerekam()

    # Selisih harus JAUH di luar toleransi: `reconcile.TOLERANSI_IDR` = 1
    # rupiah, jadi selisih 1 vs 2 justru dianggap COCOK dan tidak pernah
    # menghentikan apa pun.
    keluar = _satu_siklus(conn, telegram=telegram, transport=transport,
                          shadow=False,
                          db_state={"IDR": 1_000_000},
                          exchange_state={"IDR": 2_000_000})

    assert keluar == "halted"
    assert telegram.konfirmasi == []
    assert any("Selisih IDR" in baris for baris in telegram.lines)
    assert transport.posted == []
    assert transport.queried == []
    # Jejak audit: hanya "halted", tidak ada usulan yang pernah/usul formed.
    hasil = conn.execute("SELECT outcome FROM proposals").fetchall()
    assert [baris[0] for baris in hasil] == ["halted"]


def test_rekonsiliasi_dilewati_bila_salah_salah_satu_tidak_disuntikkan(tmp_path):
    """Hanya `db_state` (atau hanya `exchange_state`) = rekonsiliasi dilewati."""
    conn = _fresh_db(tmp_path)
    assert _satu_siklus(conn, db_state={"IDR": 1}) == "proposed"
    conn2 = _fresh_db(tmp_path)
    assert _satu_siklus(conn2, exchange_state={"IDR": 2}) == "proposed"


def test_izin_tarik_dana_menghentikan_tick_sebelum_usulan(tmp_path):
    """canWithdraw == True = berhenti apa pun kondisi lain (spec §6.6)."""
    conn = _fresh_db(tmp_path)
    transport = _TransportPerekam(can_trade=True, can_withdraw=True)
    telegram = _TelegramPerekam()

    keluar = _satu_siklus(conn, telegram=telegram, transport=transport,
                          shadow=False)

    assert keluar == "halted"
    assert telegram.konfirmasi == []
    assert any("tarik dana" in baris for baris in telegram.lines)
    assert transport.posted == []


def test_value_error_dari_engine_dianggap_berhenti_dan_diam(tmp_path, monkeypatch):
    """ValueError (provenance hilang) = halt SEPI, tanpa diulang."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    panggil = []

    def tick_boom(*args, **kwargs):
        panggil.append(1)
        raise ValueError("Aksi SELL btc_idr tidak punya nama aturan terdaftar")

    monkeypatch.setattr(engine, "tick", tick_boom)
    keluar = _satu_siklus(conn, telegram=telegram)

    assert keluar == "halted"
    assert panggil == [1]          # tepat satu kali: tidak diulang
    assert telegram.lines == []     # diam: tidak membanjiri pengguna


def test_v1_tidak_pernah_memanggil_rebalance():
    """v1 = DCA + take-profit + reserve buy. Rebalance itu v2."""
    sumber = inspect.getsource(main_loop)
    assert "rebalance_actions" not in sumber


def test_loop_tidak_membuat_tabel_sendiri():
    """R-G: satu pembuat skema. Loop hanya MEMBACA lewat dca_window."""
    assert "CREATE TABLE" not in inspect.getsource(main_loop)


# --------------------------------------------------------------------------
# Polling Telegram — satu-satunya konsumen getUpdates (R-3)
# --------------------------------------------------------------------------

def test_polling_membaca_jawaban_dan_mencatat_setuju(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    _satu_siklus(conn, telegram=telegram)          # Proposals formed
    id_usulan = telegram.konfirmasi[0].id

    fetch = _FetchPerekam([_update_tombol("setuju", id_usulan)])
    # `ctx` yang `prices`-nya terisi: on_answer membacanya untuk gerbang geser 0,5%.
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=1),
                 ctx=_ctx(), fetch=fetch)

    assert fetch.panggilan[0][2] == 0              # timeout=0, bukan blocking
    baris = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (id_usulan,)
    ).fetchone()
    assert baris[0] == "setuju"
    # Tombol di pesan lama sudah tidak berlaku — pengguna diberi tahu.
    assert any(id_usulan in baris for baris in telegram.lines)


def test_jawaban_kedua_diabaikan_dan_tidak_mengirim_order_lagi(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    _satu_siklus(conn, telegram=telegram)
    id_usulan = telegram.konfirmasi[0].id

    fetch = _FetchPerekam([
        _update_tombol("setuju", id_usulan),
        _update_tombol("setuju", id_usulan),
    ])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=1),
                 ctx=_ctx(), fetch=fetch)

    hasil = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE id = ? AND outcome = 'setuju'",
        (id_usulan,),
    ).fetchone()[0]
    assert hasil == 1
    diabaikan = [b for b in telegram.lines if "diabaikan" in b]
    assert len(diabaikan) == 1


def test_tombol_batal_menolak_tahu_bila_bulan_sudah_selesai(tmp_path):
    """`mark_batal` menolak (False) → pengguna diberi tahu (kabel Task 13 #8).

    Diperbarui untuk F46: jawaban atas id usulan TIDAK dikenal tidak boleh
    menyentuh jendela (id tak dikenal = jenis usulan tak diketahui = ragu
    = tidak menulis), jadi tes ini mendaftarkan usulan DCA SUNGGUHAN lewat
    `main_loop._ingat` dan menekan tombol pada id itu. Niat aslinya — Batal
    pada bulan yang sudah selesai menghasilkan pesan penolakan — utuh.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    dca_window.mark_confirmed(conn, _ts(hour=10))
    p = _usulan_dca(_ts(hour=10))
    main_loop._ingat([p])

    fetch = _FetchPerekam([_update_tombol("batal", p.id)])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx_kosong(), fetch=fetch)

    assert any("Bulan ini sudah selesai" in b for b in telegram.lines)
    # Keputusan confirmed tidak terhapus oleh tombol yang ditolak.
    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "confirmed"


def test_jawaban_setuju_menandai_dca_bulan_ini_confirmed(tmp_path):
    """Tanpa `mark_confirmed`, pengingat tetap menyala dan +24 jam tercipta
    catatan `dca_missed` palsu (kabel Task 13 #7).

    Diperbarui untuk F46 (lihat tes di atas): tombol ditekan pada usulan
    DCA SUNGGUHAN yang didaftarkan lewat `main_loop._ingat`, bukan pada id
    tak dikenal yang tidak boleh menyentuh jendela. Niat aslinya — Setuju
    pada usulan DCA menutup jendela menjadi confirmed — utuh.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    p = _usulan_dca(_ts(hour=9))
    main_loop._ingat([p])

    fetch = _FetchPerekam([_update_tombol("setuju", p.id)])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx_kosong(), fetch=fetch)

    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "confirmed"


def test_perintah_ulang_mengusulkan_langsung_dengan_data_baru(tmp_path,
                                                              monkeypatch):
    """/ulang DIBACA polling dan diteruskan ke `apply_ulang` pada siklus ini.

    Yang dipin di sini adalah pengiriman JAWABANNYA: teks `/ulang` harus
    benar-benar dikonsumsi polling dan diteruskan ke `apply_ulang` dengan
    `now` siklus berjalan. Tanpa polling (fetch tidak disuntikkan) penerusan
    itu mustahil terjadi, jadi tes ini benar-benar membedakan perintah dari
    denyut biasa. (Denyut biasa memang menghasilkan usulan identik dari
    konteks dan waktu yang sama — karena itu keberadaan usulan saja tidak
    cukup; lihat mutasi E1 di laporan Task 14.)
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    fetch = _FetchPerekam([_update_teks("/ulang")])

    # Perekam penerusan: `apply_ulang` asli tetap dijalankan apa adanya.
    diteruskan = []
    asli = main_loop.apply_ulang

    def perekam(conn_asli, ctx_asli, **kw):
        diteruskan.append(kw["now"])
        return asli(conn_asli, ctx_asli, **kw)

    monkeypatch.setattr(main_loop, "apply_ulang", perekam)

    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=2),
                 ctx=_ctx(), fetch=fetch)

    # Jawaban dikonsumsi: `apply_ulang` dipanggil TEPAT sekali, dengan waktu
    # siklus ini — bukan ditunda ke denyut berikutnya.
    assert diteruskan == [_ts(hour=12, minute=2)]
    assert len(telegram.konfirmasi) == 1
    assert telegram.konfirmasi[0].price_ref == 1400.0
    assert telegram.konfirmasi[0].created_ts == _ts(hour=12, minute=2)


# --------------------------------------------------------------------------
# apply_ulang — hanya pengguna yang memicu, datanya baru
# --------------------------------------------------------------------------

def test_ulang_mengusulkan_langsung_dengan_harga_terbaru(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()

    usulan = main_loop.apply_ulang(
        conn, _ctx(price=1400.0), now=9999.0, telegram=telegram,
        shadow=True, verdict=lambda pair, **kwargs: (True, 1.0),
    )

    assert usulan and usulan[0].price_ref == 1400.0


def test_ulang_tidak_memakai_angka_usulan_lama(tmp_path):
    """Usulan ulang harus dihitung dari data SAAT INI, bukan proposal lama."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    yang_lama = main_loop.apply_ulang(
        conn, _ctx(price=1400.0), now=1000.0, telegram=telegram,
        shadow=True, verdict=_ok_verdict,
    )
    yang_baru = main_loop.apply_ulang(
        conn, _ctx(price=1550.0), now=2000.0, telegram=telegram,
        shadow=True, verdict=_ok_verdict,
    )

    assert yang_baru[0].price_ref == 1550.0
    assert yang_baru[0].id != yang_lama[0].id
    assert yang_baru[0].created_ts == 2000.0


def test_ulang_mencatat_keputusan_langsung(tmp_path):
    """`/ulang` juga harus masuk jejak audit lewat `shadow_log`."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    usulan = main_loop.apply_ulang(
        conn, _ctx(price=1400.0), now=3000.0, telegram=telegram,
        shadow=True, verdict=_ok_verdict,
    )
    hasil = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (usulan[0].id,)
    ).fetchone()
    assert hasil[0] == "proposed"


# --------------------------------------------------------------------------
# Pengingat DCA — maksimal satu pesan per slot (kabel Task 13 #1)
# --------------------------------------------------------------------------

def test_pengingat_dikirim_maksimal_satu_kali_per_slot(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))

    _satu_siklus(conn, telegram=telegram, now=_ts(hour=15, minute=0),
                 ctx=_ctx_kosong())
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=15, minute=30),
                 ctx=_ctx_kosong())

    pengingat = [b for b in telegram.lines if "Pengingat" in b]
    assert len(pengingat) == 1


def test_pengingat_slot_berikutnya_tetap_dikirim(tmp_path):
    """Dedupe per SLOT, bukan sekali seumur bot."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))

    _satu_siklus(conn, telegram=telegram, now=_ts(hour=15, minute=0),
                 ctx=_ctx_kosong())
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=21, minute=0),
                 ctx=_ctx_kosong())

    assert len([b for b in telegram.lines if "Pengingat" in b]) == 2


# --------------------------------------------------------------------------
# main() — tidak pernah bisa membuat transport (R-2)
# --------------------------------------------------------------------------

def test_main_tanpa_pabrik_transport_melcatat_eksekusi_mati(tmp_path, capsys):
    conn = _fresh_db(tmp_path)
    tidur = []

    keluar = main_loop.main(
        ["--siklus", "1"],
        conn=conn,
        telegram=_TelegramPerekam(),
        jam=lambda: _ts(hour=12),
        tidur=tidur.append,
    )

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert "eksekusi" in keluaran.lower()
    assert "belum dinyalakan" in keluaran.lower()
    # Kontrak dua-irama (Task shadow runner): bangun loop kini tiap POLL_GAP_S;
    # denyut penuh (tick) tetap per HEARTBEAT_S — lihat tes dua irama.
    assert tidur == [main_loop.POLL_GAP_S]


def test_main_tidak_pernah_memanggil_pabrik_transport_saat_sakelar_mati(
        tmp_path, capsys):
    """Sakelar belum menyala -> pabrik TIDAK dipanggil, walau tersedia."""
    conn = _fresh_db(tmp_path)
    dipanggil = []

    def pabrik():
        dipanggil.append(1)
        return _TransportPerekam()

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        transport_factory=pabrik, jam=lambda: _ts(hour=12), tidur=lambda s: None,
    )

    assert keluar == 0
    assert dipanggil == []


def test_main_di_nyalakan_setelah_7_hari_memanggil_pabrik_transport(
        tmp_path, capsys):
    """7 hari tanpa penolakan menyala: pabrik dipanggil, keluar 0."""
    conn = _fresh_db(tmp_path)
    dipanggil = []

    main_loop.set_shadow_day(conn, 7)  # v1_enabled = True

    def pabrik():
        dipanggil.append(1)
        return _TransportPerekam(can_trade=True, can_withdraw=False)

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        transport_factory=pabrik, jam=lambda: _ts(hour=12),
        tidur=lambda s: None,
    )

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert dipanggil == [1]           # pabrik benar-benar dipanggil sekali
    assert "eksekusi belum dinyalakan" not in keluaran.lower()


def test_main_tidak_pernah_membangun_transport_bfisik():
    """R-2: tidak ada kelas transport pun di repo; main tidak mengarangnya."""
    sumber = inspect.getsource(main_loop.main)
    assert "IndodaxTransport" not in sumber
    assert "Transport(" not in sumber
    # Satu-satunya jalan ke transport adalah argumen yang disuntik pemanggil.
    assert "transport_factory" in sumber


def test_main_kembali_satu_bila_gagal_benar(tmp_path):
    """Kegagalan buka basis data = keluar 1, sama seperti `scheduler.main`."""
    # `conn` sengaja TIDAK diberikan: kalau yang diuji adalah kegagalan membuka
    # basis data, basis datanya harus benar-benar belum ada.
    keluar = main_loop.main(
        ["--siklus", "1"],
        db_path=os.path.join(str(tmp_path), "tidak-ada", "x.db"),
        telegram=_TelegramPerekam(),
        jam=lambda: _ts(hour=12),
        tidur=lambda s: None,
    )
    assert keluar == 1


def test_telegram_belum_diset_menghentikan_bot(tmp_path, monkeypatch):
    """Gerbang adalah inti; tanpa token bot tidak bisa apa pun."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(main_loop, "_env", lambda kunci: "")
    keluar = main_loop.main(["--siklus", "1"], conn=conn,
                            jam=lambda: _ts(hour=12), tidur=lambda s: None)
    assert keluar == 1


def test_denyut_jantung_diluar_jam_aktif_tidak_menjalankan_siklus(
        tmp_path, capsys):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=telegram,
        jam=lambda: _ts(hour=3), tidur=lambda s: None,
    )
    assert keluar == 0
    assert telegram.lines == []
    assert telegram.konfirmasi == []


# --------------------------------------------------------------------------
# DCA bulanan per siklus — jendela hanya saat terkirim (F45) + F46
# --------------------------------------------------------------------------

def test_siklus_pertama_bulan_mengusulkan_dca_dan_membuka_jendela(tmp_path):
    """Siklus pertama bulan ini (tanggal 1, 09.05) mengusulkan DCA dan
    BARULAH membuka jendela: `mark_pending` mencatat waktu yang SAMA
    dengan pengiriman — bukan saat aksi disusun (mekanisme terkunci desain
    Task 15)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(day=1, hour=9, minute=5)

    keluar = _satu_siklus(conn, telegram=telegram, now=now, ctx=_ctx_dca())

    assert keluar == "proposed"
    dca = [p for p in telegram.konfirmasi if p.rule == rules.RULE_DCA]
    assert dca, (
        "usulan DCA tidak terkirim; konfirmasi: "
        f"{[(p.rule, p.pair) for p in telegram.konfirmasi]}")
    assert all(p.window_s == 86400 for p in dca), (
        f"jendela usulan DCA: {[(p.pair, p.window_s) for p in dca]}")
    baris = conn.execute(
        "SELECT last_dca_date, dca_status, dca_sent_ts FROM bot_state "
        "WHERE id = 1"
    ).fetchone()
    assert baris["last_dca_date"] == time.strftime("%Y-%m", time.localtime(now))
    assert baris["dca_status"] == "awaiting"
    assert baris["dca_sent_ts"] == now


def test_siklus_berikutnya_tidak_mengusulkan_dca_lagi(tmp_path):
    """Bulan ini sudah tercatat -> sakelar per-siklus mati: siklus 5 menit
    kemudian tidak mengirim SATU pun usulan baru (percobaan sekali per
    bulan, putusan pengguna 2026-10-17)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    _satu_siklus(conn, telegram=telegram, now=_ts(day=1, hour=9, minute=5),
                 ctx=_ctx_dca())
    sebelum = len(telegram.konfirmasi)
    assert sebelum > 0, "siklus pertama harus benar-benar mengirim usulan"

    keluar = _satu_siklus(conn, telegram=telegram,
                          now=_ts(day=1, hour=9, minute=10), ctx=_ctx_dca())

    assert keluar == "kosong"
    assert telegram.konfirmasi[sebelum:] == [], (
        "usulan baru terkirim pada siklus kedua: "
        f"{[(p.rule, p.pair) for p in telegram.konfirmasi[sebelum:]]}")


def test_dca_gagal_di_gerbang_menutup_bulan_sekali(tmp_path):
    """Percobaan pertama gugur di gerbang spread -> `mark_skip` menutup
    bulan tepat sekali: `dca_status` "batal", `dca_sent_ts` NULL (tidak ada
    pesan yang pernah terkirim), dan siklus berikutnya TIDAK mengulang
    percobaan — tidak ada `spread_rejected` baru yang meracuni shadow day."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(day=1, hour=9, minute=5)

    keluar = _satu_siklus(conn, telegram=telegram, now=now, ctx=_ctx_dca(),
                          verdict=lambda pair, **kwargs: (False, 99.0))

    assert keluar == "kosong"          # tidak ada usulan yang terkirim
    baris = conn.execute(
        "SELECT last_dca_date, dca_status, dca_sent_ts FROM bot_state "
        "WHERE id = 1"
    ).fetchone()
    assert baris["last_dca_date"] == time.strftime("%Y-%m", time.localtime(now))
    assert baris["dca_status"] == "batal"
    assert baris["dca_sent_ts"] is None

    gagal = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE outcome = 'spread_rejected'"
    ).fetchone()[0]
    assert gagal > 0, "fixture harus benar-benar ditolak gerbang spread"

    _satu_siklus(conn, telegram=telegram, now=_ts(day=1, hour=9, minute=10),
                 ctx=_ctx_dca(), verdict=lambda pair, **kwargs: (False, 99.0))

    sesudah = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE outcome = 'spread_rejected'"
    ).fetchone()[0]
    assert sesudah == gagal, (
        "siklus kedua mengulang percobaan DCA — bulan harusnya sudah tutup")


def test_setuju_take_profit_tidak_menutup_jendela_dca(tmp_path):
    """F46: Setuju pada usulan TAKE-PROFIT tidak boleh menyentuh jendela
    DCA yang sedang `awaiting` — jendela hanya ditutup oleh jawaban atas
    usulan DCA sendiri."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    p = _usulan_take_profit(_ts(hour=12))
    main_loop._ingat([p])

    fetch = _FetchPerekam([_update_tombol("setuju", p.id)])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=1),
                 ctx=_ctx(), fetch=fetch)

    # Jawaban tetap diproses engine (bukan dibuang) ...
    baris = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (p.id,)
    ).fetchone()
    assert baris[0] == "setuju"
    # ... tetapi jendela DCA tetap menunggu (F46).
    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "awaiting"


def test_setuju_dca_menutup_jendela(tmp_path):
    """Setuju pada usulan DCA menutup jendela 24 jam -> "confirmed"
    (kabel Task 13 #7: tanpa ini pengingat menyala terus dan +24 jam
    tercipta catatan `dca_missed` palsu)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    p = _usulan_dca(_ts(hour=9))
    main_loop._ingat([p])

    fetch = _FetchPerekam([_update_tombol("setuju", p.id)])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx(), fetch=fetch)

    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "confirmed"


def test_batal_pada_usulan_bukan_dca_tidak_menyentuh_jendela(tmp_path):
    """F46 untuk Batal: tombol Batal pada usulan take-profit bukan urusan
    DCA — jendela tetap `awaiting` DAN pengguna tidak dikirimi kabar bohong
    "Bulan ini sudah selesai" (pesan itu hanya untuk tombol Batal pada
    usulan DCA)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    p = _usulan_take_profit(_ts(hour=12))
    main_loop._ingat([p])

    fetch = _FetchPerekam([_update_tombol("batal", p.id)])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=1),
                 ctx=_ctx(), fetch=fetch)

    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "awaiting"
    assert not any("Bulan ini sudah selesai" in b for b in telegram.lines), (
        "pesan 'Bulan ini sudah selesai' hanya untuk Batal pada usulan DCA")


def test_jawaban_id_tak_dikenal_tidak_menyentuh_jendela_dca(tmp_path):
    """F46 jalur "id tak dikenal": lookup gagal = jendela TIDAK disentuh,
    DAN pengguna tetap diberi kabar — dua jawaban atas id yang tidak
    terdaftar berbagi satu siklus (Setuju lalu Batal), masing-masing
    mendapat satu pesan "tidak dikenal". Tanpa pesan itu pengguna hanya
    menekan tombol yang diam-diam tidak melakukan apa pun; tanpa gerbang
    jendelanya, tombol basi bisa mengubah bulan yang sedang menunggu.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))

    fetch = _FetchPerekam([
        _update_tombol("setuju", "usulan-tak-ada"),
        _update_tombol("batal", "usulan-juga-tak-ada"),
    ])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx_kosong(), fetch=fetch)

    # (a) Jendela DCA tetap menunggu — id tak dikenal tidak menulis apa pun.
    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "awaiting"
    # (b) Kedua jawaban dijawab jujur, masing-masing TEPAT sekali.
    dikenal = [b for b in telegram.lines if "tidak dikenal" in b]
    assert len(dikenal) == 2, f"pesan 'tidak dikenal': {dikenal}"
    assert any("usulan-tak-ada" in b for b in dikenal)
    assert any("usulan-juga-tak-ada" in b for b in dikenal)


def test_batal_lalu_setuju_pada_usulan_dca_yang_sama_tidak_mengirim_order(
        tmp_path):
    """Batal (mark_batal True) LALU Setuju pada id DCA YANG SAMA: jendela
    sudah ditutup resmi, jadi Setuju lanjutan harus inert — nol order dan
    `dca_status` tetap 'batal'. Tanpa pelepasan ingatan di `_jawab`,
    Setuju lanjutan menemukan proposalnya masih terdaftar, `mark_confirmed`
    no-op (baris bukan 'awaiting'), tetapi `on_answer` TETAP mengirim order
    sungguhan — order keluar tanpa jendela yang sah (temuan review final;
    inkonsistensi audit)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    p = _usulan_dca(_ts(hour=9))
    main_loop._ingat([p])

    # Mode bayangan MATI + transport perekam: satu-satunya konfigurasi yang
    # bisa MEMBUKTIKAN lubangnya (order benar-benar terkirim). Tanpa ini
    # tes gagal sebagai kecelakaan (F40), bukan sebagai pernyataan.
    transport = _TransportPerekam(can_trade=True, can_withdraw=False,
                                  status="FILLED")
    ctx = _ctx(price=1400.0)
    # Saldo cukup besar supaya gerbang saldo tidak menolak — tanpa ini
    # jawaban Setuju berhenti di "gate_blocked" dan lubangnya tidak pernah
    # terbukti mengirim order (tes jadi tidak peka terhadap cacatnya).
    ctx.free_idr = 400000.0

    fetch = _FetchPerekam([
        _update_tombol("batal", p.id),
        _update_tombol("setuju", p.id),
    ])
    _satu_siklus(conn, telegram=telegram, transport=transport, shadow=False,
                 now=_ts(hour=12), ctx=ctx, fetch=fetch, verdict=_ok_verdict)

    # (a) NOL order — jendela sudah ditutup Batal; Setuju lanjutan inert.
    assert transport.posted == [], (
        f"order terkirim setelah Batal menutup jendela: {transport.posted}")
    # (b) Audit konsisten: bulan tetap 'batal' — tidak ada order tanpa
    #     jendela yang sah, dan tidak ada flip diam-diam ke 'confirmed'.
    baris = conn.execute(
        "SELECT dca_status FROM bot_state WHERE id = 1").fetchone()
    assert baris["dca_status"] == "batal"
    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "none"
    # (c) Pengguna dijawab jujur: Setuju lanjutan jatuh ke jalur "id tak
    #     dikenal" — tombol pada pesan yang jendelanya sudah ditutup tidak
    #     berlaku lagi.
    assert any(p.id in b and "tidak dikenal" in b for b in telegram.lines)
    # (d) Proposal tidak "hidup kembali": tidak ada outcome setuju/executed
    #     yang tercatat untuk id ini. Tabel yang belum pernah dibuat = nol
    #     catatan (pola yang sama dengan `engine._outcome_log`).
    try:
        hidup = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE id = ? AND outcome IN "
            "('setuju', 'executed')", (p.id,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        hidup = 0
    assert hidup == 0


def test_siklus_sebelum_jam_usul_tidak_menutup_bulan(tmp_path):
    """Guard `ctx.dca_enabled`: siklus di luar jendela usul (tanggal 1,
    08.00 — sebelum `DCA_SEND_HOUR`) tidak boleh menyentuh bulan sama
    sekali. Tanpa guard itu `_catat_dca` memanggil `mark_skip` jam 08.00
    dan percobaan yang sah pukul 09.05 mustahil terjadi — bulan sudah
    ditutup sebelum sempat dicoba."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    _satu_siklus(conn, telegram=telegram, now=_ts(day=1, hour=8),
                 ctx=_ctx_dca())

    baris = conn.execute(
        "SELECT last_dca_date FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] is None, (
        "siklus di luar jendela usul tidak boleh menutup bulan")

    # Kontrol: siklus 09.05 sesudahnya MASIH mengusulkan DCA.
    keluar = _satu_siklus(conn, telegram=telegram,
                          now=_ts(day=1, hour=9, minute=5), ctx=_ctx_dca())
    assert keluar == "proposed"


def test_ulang_pada_jam_usul_dca_mencatat_jendela(tmp_path):
    """/ulang pada jam usul: konfirmasi DCA terkirim lewat `apply_ulang`
    DAN `bot_state` ikut tercatat `awaiting` — `engine.tick` siklus memang
    dilewati (id kembar), tetapi penanda jendela tetap tertulis."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(day=1, hour=9, minute=5)
    fetch = _FetchPerekam([_update_teks("/ulang")])

    _satu_siklus(conn, telegram=telegram, now=now, ctx=_ctx_dca(), fetch=fetch)

    dca = [p for p in telegram.konfirmasi if p.rule == rules.RULE_DCA]
    assert dca, "konfirmasi DCA harus terkirim lewat apply_ulang"
    assert all(p.window_s == 86400 for p in dca), (
        f"jendela usulan DCA: {[(p.pair, p.window_s) for p in dca]}")
    assert all(p.created_ts == now for p in dca), (
        "usulan ulang memakai waktu siklus berjalan")
    baris = conn.execute(
        "SELECT last_dca_date, dca_status, dca_sent_ts FROM bot_state "
        "WHERE id = 1"
    ).fetchone()
    assert baris["last_dca_date"] == time.strftime("%Y-%m", time.localtime(now))
    assert baris["dca_status"] == "awaiting"
    assert baris["dca_sent_ts"] == now


def test_ulang_hanya_mencatat_jendela_satu_kali(tmp_path, monkeypatch):
    """Guard `not diulang` di `tick_cycle`: `/ulang` sudah mencatat jendela
    di `apply_ulang` — siklus tidak boleh SEKALI LAGI mencoba mencatat
    (dua penulis untuk satu keputusan bulan adalah bug, bukan pengaman).
    Perekam menghitung upaya pencatatan; tanpa guard itu upaya kedua
    (`mark_skip` dari `tick_cycle` atas daftar kosong) ikut terhitung."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(day=1, hour=9, minute=5)
    panggilan = []
    asli_pending = dca_window.mark_pending
    asli_skip = dca_window.mark_skip

    def pending_terhitung(c, ts):
        panggilan.append(("mark_pending", ts))
        return asli_pending(c, ts)

    def skip_terhitung(c, ts):
        panggilan.append(("mark_skip", ts))
        return asli_skip(c, ts)

    monkeypatch.setattr(dca_window, "mark_pending", pending_terhitung)
    monkeypatch.setattr(dca_window, "mark_skip", skip_terhitung)

    fetch = _FetchPerekam([_update_teks("/ulang")])
    _satu_siklus(conn, telegram=telegram, now=now, ctx=_ctx_dca(), fetch=fetch)

    assert panggilan == [("mark_pending", now)], (
        "siklus /ulang harus mencatat jendela TEPAT satu kali (apply_ulang); "
        f"upaya tercatat: {panggilan}")


# --------------------------------------------------------------------------
# Capstone — siklus DCA penuh: usul (bayangan) -> Setuju -> SATU order (nyata)
# --------------------------------------------------------------------------

def test_dca_konfirmasi_menghasilkan_satuan_order_end_to_end(tmp_path):
    """Kapstone Task 15: usulan DCA bulanan -> Setuju -> tepat SATU order.

    Dua siklus sungguhan berurutan, tanpa produksi yang disuntik:

    1. Tanggal 1 pukul 09.05, MODE BAYANGAN (`shadow=True`): `tick_cycle`
       menghitung sakelar DCA per-siklus dari `boleh_usul_dca`, engine
       mengusulkan DCA untuk ketiga pasangan target, dan `_catat_dca`
       membuka jendela (`mark_pending`). Id usulan diambil dari
       `telegram.konfirmasi` — bukan direkayasa.
    2. Pukul 09.10, MODE HIDUP (`shadow=False`), transport perekam
       `status="FILLED"`: pengguna menekan Setuju — dua kali, dalam SATU
       fetch (tombol lama yang ditekan ganda). `_TransportPerekam` yang
       `can_trade=True` adalah konfigurasi live (bukan View-Only bayangan).

    Yang dipatok di sini adalah SATUANNYA hasil: `transport.posted` tepat
    satu payload `order_type == "limit"` dengan `client_order_id` = id
    proposal (verifikasi F7 di `executor.payload_for`), satu baris
    `order_status` dengan `status == "filled"` dan `rule == rules.RULE_DCA`
    (penulisan langkah 8 `engine.on_answer` — kolom dan nilai persis seperti
    yang dipakai tes live di tests/test_engine.py), jendela DCA `confirmed`,
    dan jawaban ganda TIDAK menghasilkan order kedua. Setiap asersi di sini
    bisa gagal: literal `posted == []` tidak pernah dipakai sebagai
    "bukti" dan payload dibaca dari daftar yang benar-benar terisi.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()

    # 1. Siklus pertama bulan ini — mode bayangan, transport tidak terpasang.
    keluar = _satu_siklus(conn, telegram=telegram, shadow=True,
                          now=_ts(day=1, hour=9, minute=5), ctx=_ctx_dca())
    assert keluar == "proposed"
    dca = [p for p in telegram.konfirmasi if p.rule == rules.RULE_DCA]
    assert dca, (
        "usulan DCA tidak terkirim; konfirmasi: "
        f"{[(p.rule, p.pair) for p in telegram.konfirmasi]}")
    id_usulan = dca[0].id

    # 2. Siklus berikutnya — mode hidup, transport perekam siap mencatat
    #    SETIAPA order. Harga sama persis supaya gerbang geser 0,5% tidak
    #    menggugurkan konfirmasi (angka di pesan harus masih valid).
    transport = _TransportPerekam(can_trade=True, can_withdraw=False,
                                  status="FILLED")
    fetch = _FetchPerekam([
        _update_tombol("setuju", id_usulan),
        _update_tombol("setuju", id_usulan),   # jawaban ganda dalam satu fetch
    ])
    _satu_siklus(conn, telegram=telegram, transport=transport, shadow=False,
                 now=_ts(day=1, hour=9, minute=10), ctx=_ctx_dca(),
                 verdict=_ok_verdict, fetch=fetch)

    # Tepat SATU order keluar — bukan nol, bukan dua.
    assert len(transport.posted) == 1, (
        f"payload terkirim: {transport.posted}")
    payload = transport.posted[0]
    # Nama kunci payload persis seperti yang dibangun executor.payload_for
    # (pola tests/test_engine.py dan tests/test_executor.py): order_type,
    # bukan "type" — "type" adalah sisi order (buy/sell).
    assert payload["order_type"] == "limit"
    assert payload["type"] == "buy"
    assert payload["pair"] == dca[0].pair
    # Gerbang verifikasi F7: client_order_id = id proposal yang dikirim.
    assert payload["client_order_id"] == id_usulan

    # Satu baris order_status dengan status final dan nama aturan DCA
    # (penulisan langkah 8 `engine.on_answer`; kolom & nilai dari skema
    # nyata di `engine._tulis_status_order`).
    baris_status = conn.execute(
        "SELECT status, rule FROM order_status WHERE client_order_id = ?",
        (id_usulan,),
    ).fetchone()
    assert baris_status is not None, "tidak ada baris order_status tertulis"
    assert baris_status["status"] == "filled"
    assert baris_status["rule"] == rules.RULE_DCA
    jumlah_baris = conn.execute("SELECT COUNT(*) FROM order_status").fetchone()[0]
    assert jumlah_baris == 1

    # Jendela DCA bulan ini tertutup "confirmed" oleh tombol Setuju (F46).
    assert dca_window.dca_state(conn, _ts(day=1, hour=9, minute=10))[0] == (
        "confirmed")
    # Jawaban ganda berhenti di langkah 0 `on_answer` — order kedua tidak
    # pernah terkirim (len(transport.posted) di atas dihitung SETELAH
    # kedua jawaban diproses dalam satu fetch).


# --------------------------------------------------------------------------
# Pembaca pasar produksi — baca_konteks (periode shadow)
# --------------------------------------------------------------------------

def test_baca_konteks_mengisi_konteks_penuh(tmp_path):
    import portfolio
    import portfolio_store

    conn = _fresh_db(tmp_path)
    portfolio_store.init_db(conn)

    panggilan_sinkron = []
    def sinkron(c):
        panggilan_sinkron.append(c)

    info = {"balances": [
        {"asset": "idr", "free": "150000", "locked": "50000"},
        {"asset": "eth", "free": "0.5", "locked": "0"},
    ]}
    now = _ts(hour=12)
    portfolio_store.insert_trade(conn, {
        "pair": "eth_idr", "side": "buy", "price": 400000.0, "qty": 0.1,
        "quote": 40000.0, "fee": 0, "ts": now - 100, "source": "api",
        "external_id": "e-buy-hari-ini"})
    portfolio_store.insert_trade(conn, {
        "pair": "eth_idr", "side": "sell", "price": 450000.0, "qty": 0.05,
        "quote": 22500.0, "fee": 0, "ts": now - 90000, "source": "api",
        "external_id": "e-sell-kemarin"})
    portfolio_store.insert_trade(conn, {
        "pair": "btc_idr", "side": "buy", "price": 900000000.0, "qty": 0.001,
        "quote": 900.0, "fee": 0, "ts": now - 7200, "source": "api",
        "external_id": "b-buy-2jam"})

    ctx = main_loop.baca_konteks(
        conn, now=now, sinkron=sinkron,
        ambil_info=lambda: info, ambil_harga=lambda pair: 1000.0)

    assert panggilan_sinkron == [conn]
    # free = hanya yang bebas; locked (50.000) TIDAK boleh dihitung pakai.
    assert ctx.free_idr == 150000.0
    # Tidak ada konfigurasi cadangan terkunci -> 0, tanpa mengarang angka.
    assert ctx.reserve_idr == 0.0
    assert set(ctx.prices) == set(portfolio.COIN_PAIRS.values())
    eth = [h for h in ctx.holdings if h.pair == "eth_idr"]
    assert len(eth) == 1 and eth[0].qty == 0.5
    # Hitung hari lokal: buy eth (now-100) dan buy btc (now-7200) sama-sama
    # hari ini; sell kemarin (now-90000) tidak ikut.
    assert ctx.today_counts == {"eth_idr": 1, "btc_idr": 1}
    assert ctx.last_fill_ts["eth_idr"] == now - 100
    assert ctx.held_pairs_24h == {"eth_idr", "btc_idr"}


def test_baca_konteks_harga_satu_pair_gagal_mengangkat(tmp_path):
    """Satu pair gagal harga = TANPA angka parsial: raise, denyut dilewati."""
    import portfolio_store
    conn = _fresh_db(tmp_path)
    portfolio_store.init_db(conn)

    def ambil_harga(pair):
        return None if pair == "btc_idr" else 1000.0

    with pytest.raises(ValueError):
        main_loop.baca_konteks(
            conn, sinkron=lambda c: None,
            ambil_info=lambda: {"balances": []}, ambil_harga=ambil_harga)


def test_baca_konteks_bentuk_saldo_tak_dikenal_mengangkat(tmp_path):
    """Bukan bentuk TAPIv2 = tidak ada angka free yang bisa dipercaya."""
    import portfolio_store
    conn = _fresh_db(tmp_path)
    portfolio_store.init_db(conn)
    with pytest.raises(ValueError):
        main_loop.baca_konteks(
            conn, sinkron=lambda c: None, ambil_info=lambda: {},
            ambil_harga=lambda pair: 1000.0)


def test_baca_konteks_idr_dikunci_tidak_dihitung_free(tmp_path):
    import portfolio_store
    conn = _fresh_db(tmp_path)
    portfolio_store.init_db(conn)
    info = {"balances": [{"asset": "idr", "free": "0", "locked": "70000"}]}
    ctx = main_loop.baca_konteks(
        conn, sinkron=lambda c: None, ambil_info=lambda: info,
        ambil_harga=lambda pair: 1000.0)
    assert ctx.free_idr == 0.0
    assert ctx.holdings == []


# --------------------------------------------------------------------------
# Loop dua irama — bangun 15 dtk (polling tombol), denyut penuh 300 dtk
# --------------------------------------------------------------------------

def test_dua_irama_tick_tiap_heartbeat_polling_tiap_bangun(tmp_path, monkeypatch):
    conn = _fresh_db(tmp_path)
    denyut = []
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: denyut.append(now))
    fetch = _FetchPerekam([])
    jam = [_ts(hour=12)]
    tidur_panggilan = []

    def tidur(s):
        tidur_panggilan.append(s)
        jam[0] += s

    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)

    assert keluar == 0
    # Tick penuh HANYA di bangun pertama; dua bangun berikutnya (< 300 dtk)
    # cukup polling tombol — itulah inti dua irama.
    assert len(denyut) == 1
    assert len(fetch.panggilan) == 3       # denyut + 2 inter-denyut
    assert tidur_panggilan == [main_loop.POLL_GAP_S] * 3


def test_pembaca_gagal_denyut_dilewati_polling_ditahan(
        tmp_path, monkeypatch, capsys):
    conn = _fresh_db(tmp_path)
    denyut = []
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: denyut.append(now))
    fetch = _FetchPerekam([])
    panggilan_pembaca = []

    def pembaca(c):
        panggilan_pembaca.append(c)
        if len(panggilan_pembaca) >= 2:
            raise RuntimeError("jaringan putus")
        return main_loop._konteks_kosong()

    dasar = _ts(hour=12)
    urutan = [dasar, dasar + 15, dasar + 300, dasar + 315]
    indeks = [0]

    def jam():
        nilai = urutan[min(indeks[0], len(urutan) - 1)]
        indeks[0] += 1
        return nilai

    keluar = main_loop.main(
        ["--siklus", "4"], conn=conn, telegram=_TelegramPerekam(),
        pembaca_konteks=pembaca, fetch=fetch, jam=jam, tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    # Pembaca gagal di denyut 2 -> TANPA tick tambahan, TANPA polling lanjutan.
    assert len(denyut) == 1
    assert len(fetch.panggilan) == 2       # denyut + satu inter sebelum gagal
    # Dua kegagalan beruntun = SATU baris log, bukan banjir.
    assert keluaran.count("Pembaca pasar gagal") == 1
    assert "Gagal menjalankan loop" not in keluaran


def test_polling_antar_denyut_gagal_loop_tetap_hidup(
        tmp_path, monkeypatch, capsys):
    conn = _fresh_db(tmp_path)
    denyut = []
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: denyut.append(now))
    panggilan = {"n": 0}

    def fetch(token, *, offset, timeout):
        panggilan["n"] += 1
        if panggilan["n"] == 2:
            raise ConnectionError("jaringan putus")
        return {"result": []}

    jam = [_ts(hour=12)]

    def tidur(s):
        jam[0] += main_loop.POLL_GAP_S

    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)

    keluaran = capsys.readouterr().out
    # Gagal baca Telegram = dilewati sekali, loop tetap hidup, lalu pulih.
    assert keluar == 0
    assert panggilan["n"] == 3
    assert keluaran.count("Polling Telegram gagal") == 1
    assert len(denyut) == 1
    assert "Gagal menjalankan loop" not in keluaran


def test_sapa_awal_hanya_bila_ada_fetch(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    fetch = _FetchPerekam([])

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=telegram, fetch=fetch,
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0
    assert telegram.lines, "sapa awal harus terkirim begitu fetch ada"
    assert "mode bayangan" in telegram.lines[0].lower()


def test_banner_mode_bayangan_dicetak_sekali_saja(tmp_path, capsys):
    """Banner status = sekali per proses, bukan log denyut tiap 15 detik.

    Tanpa penanda sekali-cetak ini, banner tercetak tiap bangun (5.760
    baris/hari pada irama 15 dtk) dan menelan perhatian dari log kegagalan
    yang asli. Semua tes lama memakai `--siklus 1` sehingga perilaku lama
    (sekali per denyut = sekali per proses di sana) tidak berubah.
    """
    conn = _fresh_db(tmp_path)
    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0
    assert capsys.readouterr().out.count("Eksekusi belum dinyalakan") == 1


def test_banner_tanpa_pabrik_juga_dicetak_sekali(tmp_path, capsys):
    """Sakelar nyala tanpa pabrik transport: peringatan penting — tetap
    sekali per proses. Keduanya dicetak on-demand saat statusnya BERUBAH,
    bukan sebagai detak jantung log."""
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, main_loop.GO_LIVE_SHADOW_DAYS)
    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0
    keluaran = capsys.readouterr().out
    assert keluaran.count("tidak ada pabrik transport") == 1
    assert "berjalan dalam mode bayangan" not in keluaran


def test_denyut_penuh_gagal_tidak_membunuh_loop(tmp_path, monkeypatch, capsys):
    """Galat jaringan di denyut penuh = denyut dilewati, proses TETAP hidup.

    Nyata 2026-10-18 17.54: satu timeout api.telegram.org membunuh bot lewat
    handler luar (`return 1`) — task scheduler tidak merestart (hasil 0) dan
    hitungan G5 berhenti. Kontrak baru: kegagalan denyut dicetak SEKALI per
    rentetan, ditunggangi bangun berikutnya (15 dtk), penanda denyut tidak
    maju saat gagal — sama seperti penjaga `pembaca` dan `polling`.
    """
    conn = _fresh_db(tmp_path)
    hitung = {"panggil": 0}

    def denyut_meledak(*_, **__):
        hitung["panggil"] += 1
        raise RuntimeError("Read timed out. (read timeout=10)")

    monkeypatch.setattr(main_loop, "tick_cycle", denyut_meledak)
    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0, "proses harus selamat dari galat denyut"
    keluaran = capsys.readouterr().out
    assert keluaran.count("Denyut penuh gagal") == 1
    assert "Gagal menjalankan loop" not in keluaran
    # Penanda tidak maju saat gagal → tiap bangun mengulang percobaan.
    assert hitung["panggil"] == 3


# --------------------------------------------------------------------------
# baca_harga — harga segar saat jawaban diproses (gerbang geser 0,5%)
# --------------------------------------------------------------------------

def test_baca_harga_menyegarkan_harga_sebelum_jawaban(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(hour=12)
    p = _usulan_take_profit(now)              # price_ref = 1400
    shadow_log.record(conn, p, "proposed", "")
    main_loop._ingat([p])

    ctx = _ctx(price=900.0)                   # harga basi: 35% dari pesan
    panggilan = []

    def baca_harga(pair):
        panggilan.append(pair)
        return 1400.0                         # harga segar saat tombol ditekan

    fetch = _FetchPerekam([_update_tombol("setuju", p.id)])
    main_loop._polling(
        conn, ctx, now=now, telegram=telegram, transport=None, shadow=True,
        verdict=_ok_verdict, fetch=fetch, baca_harga=baca_harga)

    assert panggilan == ["btc_idr"]
    assert ctx.prices["btc_idr"] == 1400.0
    baris = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (p.id,)).fetchone()
    # Tanpa penyegaran: harga 900 vs pesan 1400 = hangus, outcome tetap
    # "proposed". Dengan penyegaran: konfirmasi tercatat.
    assert baris is not None and baris[0] == "setuju"


def test_baca_harga_gagal_konfirmasi_hangus(tmp_path):
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    now = _ts(hour=12)
    p = _usulan_take_profit(now)
    shadow_log.record(conn, p, "proposed", "")
    main_loop._ingat([p])

    ctx = _ctx(price=1400.0)
    fetch = _FetchPerekam([_update_tombol("setuju", p.id)])
    main_loop._polling(
        conn, ctx, now=now, telegram=telegram, transport=None, shadow=True,
        verdict=_ok_verdict, fetch=fetch, baca_harga=lambda pair: None)

    # Harga tak terbaca = tidak ada keputusan: hangus, outcome tak berubah.
    assert ctx.prices["btc_idr"] is None
    baris = conn.execute(
        "SELECT outcome FROM proposals WHERE id = ?", (p.id,)).fetchone()
    assert baris is not None and baris[0] == "proposed"
    assert any("hangus" in b for b in telegram.lines)


# --------------------------------------------------------------------------
# Blok __main__ — satu-satunya tempat jalur produksi disuntikkan
# --------------------------------------------------------------------------

def test_blokinisialisasi_memuat_jalur_produksi():
    """Jalur produksi hanya di blok `__main__`, tidak pernah di dalam `main()`.

    `main()` dipanggil berkali-kali oleh tes tanpa fetch/pembaca (perilaku
    lama persis); bila `live_fetch` masuk ke dalamnya, setiap pemanggilan tes
    akan mengarah ke jaringan dan conftest menjerit. Pola tes sumber R-2.
    """
    sumber_modul = inspect.getsource(main_loop)
    assert "live_fetch" in sumber_modul
    assert "pembaca_konteks=baca_konteks" in sumber_modul
    assert "baca_harga=portfolio.fetch_price" in sumber_modul

    sumber_main = inspect.getsource(main_loop.main)
    assert "live_fetch" not in sumber_main
    assert "pembaca_konteks=None" in sumber_main    # parameter default, bukan bacaan produksi


# --------------------------------------------------------------------------
# Review 2026-10-18 — C1/I3/I4: cabang antar-denyut
# --------------------------------------------------------------------------

def test_ulang_antar_denyut_dinaikkan_jadi_denyut_penuh(tmp_path):
    """I3/C1 (review): `/ulang` antar-denyut memakai konteks denyut terakhir
    (≤300 detik basi) dan `dca_enabled` tak pernah dihitung — usulan bisa
    lahir dari angka lama dan usulan DCA hilang diam-diam.

    Kontrak baru: bangun berisi `/ulang` DINAIKKAN jadi denyut penuh —
    pembaca jalan lagi (konteks segar), `tick_cycle` menghitung sakelar DCA,
    dan `apply_shadow_gate` menutup denyut yang sama (penolakan tidak lolos
    antar hari, C1).
    """
    conn = _fresh_db(tmp_path)
    panggilan_pembaca = []

    def pembaca(c):
        panggilan_pembaca.append(c)
        return main_loop._konteks_kosong()

    fetch = _FetchPerekam([_update_teks("/ulang")])
    keluar = main_loop.main(
        ["--siklus", "2"], conn=conn, telegram=_TelegramPerekam(),
        pembaca_konteks=pembaca, fetch=fetch,
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0
    # Bangun 1 = denyut biasa; bangun 2 (antardenyut, berisi /ulang) = denyut
    # penuh: konteks dibaca ulang dan batch diambil ulang oleh tick_cycle.
    assert len(panggilan_pembaca) == 2, "pembaca harus jalan di bangun /ulang"
    assert len(fetch.panggilan) == 3   # denyut + ambil-peek + ambil ulang tick


def test_penolakan_antar_denyut_direset_di_bangun_yang_sama(tmp_path):
    """C1 (review): penolakan yang tercatat SETELAH denyut pagi harus
    me-reset `shadow_day` di bangun yang sama.

    Skenario nyata: `/ulang` jam 22.57 mencatat `spread_rejected`, denyut
    berikutnya jatuh ke DND (07.00 besok) — `rejected_count` membaca tanggal
    BARU, nol penolakan, hari kotor terhitung bersih = G5 palsu lulus.
    """
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, 5)
    jam = [_ts(hour=12)]

    def tidur(s):
        jam[0] += main_loop.POLL_GAP_S
        # Penolakan tercatat di antara denyut (tunggu denyut = tunggu hari
        # berganti pada skenario 22.57 -> 07.00).
        shadow_log.record(conn, _usulan_take_profit(jam[0]), "spread_rejected")

    keluar = main_loop.main(
        ["--siklus", "2"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_FetchPerekam([_update_tombol("setuju", "usulan-tak-ada")]),
        jam=lambda: jam[0], tidur=tidur)

    assert keluar == 0
    assert main_loop.load_state(conn)["shadow_day"] == 0, (
        "penolakan hari ini harus direset di bangun yang sama")


def test_polling_antar_denyut_ditahan_saat_transport_ada(tmp_path):
    """I4 (review): jawaban antar-denyut pernah diproses tanpa rekonsiliasi
    dan cek izin — hari ini aman karena transport selalu None, tapi begitu
    go-live memasang `transport_factory` lubangnya terbuka.

    Kontrak baru: cabang antar-denyut hanya jalan TANPA transport; dengan
    transport, jawaban ditahan ke denyut penuh yang mendahului rekonsiliasi
    (langkah 1-2 `tick_cycle`).
    """
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, main_loop.GO_LIVE_SHADOW_DAYS)
    fetch = _FetchPerekam([])

    def pabrik():
        return _TransportPerekam()

    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        transport_factory=pabrik, fetch=fetch,
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    assert keluar == 0
    assert len(fetch.panggilan) == 1, (
        "fetch hanya di denyut penuh — polling antar-denyut ditahan")


def test_update_beracun_tidak_membuang_saudaranya(tmp_path, capsys):
    """I2 (review): offset Telegram dimajukan SEBELUM batch diproses — tanpa
    guard per update, satu balasan tak terduga di tengah antrian membuang
    sisa update diam-diam (tombol Setuju/Batal hilang tanpa jejak, pengguna
    mengira sudah terjawab).

    Kontrak baru: update beracun dicatat jujur SEKALI (offset maju = tidak
    diulang, jadi bukan banjir log) lalu dilewati; saudara sebatch tetap
    diproses.
    """
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    fetch = _FetchPerekam([
        {"update_id": 9, "callback_query": 42},   # rusak: "data" in 42 -> TypeError
        _update_tombol("tunda", "usulan-tak-ada"),
    ])

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=telegram, fetch=fetch,
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert "update" in keluaran.lower() and "gagal" in keluaran.lower(), (
        "kegagalan satu update harus tercatat, bukan sunyi")
    assert any("tidak dikenal" in b for b in telegram.lines), (
        "saudara sebatch harus tetap diproses")


# --------------------------------------------------------------------------
# Review 2026-10-18 — I1: kegagalan terus-menerus tidak boleh berakhir sunyi
# (Review Focus #2 rencana: "pemulihan dicetak sekali" — selama ini tidak
# pernah dicetak, dan kegagalan permanen hanya menghasilkan SATU baris).
# --------------------------------------------------------------------------

def test_pemulihan_polling_dicetak_setelah_gagal(tmp_path, capsys):
    """Sewaktu `gagal_polling` turun kembali, PEMULIHAN harus terlihat —
    tanpa itu kegagalan singkat dan kegagalan permanen sama-sama sunyi."""
    conn = _fresh_db(tmp_path)
    panggilan = {"n": 0}

    def fetch(token, *, offset, timeout):
        panggilan["n"] += 1
        if panggilan["n"] == 2:
            raise ConnectionError("jaringan putus")
        return {"result": []}

    jam = [_ts(hour=12)]

    def tidur(s):
        jam[0] += main_loop.POLL_GAP_S

    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert keluaran.count("Polling Telegram gagal") == 1
    assert "Polling Telegram sudah pulih" in keluaran, (
        "pemulihan harus dicetak — Review Focus #2")


def test_pemulihan_pembaca_dicetak_setelah_gagal(tmp_path, monkeypatch, capsys):
    conn = _fresh_db(tmp_path)
    denyut = []
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: denyut.append(now))
    panggilan_pembaca = []

    def pembaca(c):
        panggilan_pembaca.append(c)
        if len(panggilan_pembaca) == 2:
            raise RuntimeError("jaringan putus")
        return main_loop._konteks_kosong()

    dasar = _ts(hour=12)
    urutan = [dasar, dasar + 15, dasar + 300, dasar + 315]
    indeks = [0]

    def jam():
        nilai = urutan[min(indeks[0], len(urutan) - 1)]
        indeks[0] += 1
        return nilai

    keluar = main_loop.main(
        ["--siklus", "4"], conn=conn, telegram=_TelegramPerekam(),
        pembaca_konteks=pembaca, fetch=_FetchPerekam([]),
        jam=jam, tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert keluaran.count("Pembaca pasar gagal") == 1
    assert "Pembaca pasar sudah pulih" in keluaran


def test_pemulihan_denyut_dicetak_setelah_gagal(tmp_path, monkeypatch, capsys):
    conn = _fresh_db(tmp_path)
    hitung = {"panggil": 0}

    def denyut_semicoba(*_, **__):
        hitung["panggil"] += 1
        if hitung["panggil"] == 1:
            raise RuntimeError("Read timed out. (read timeout=10)")
        return "kosong"

    monkeypatch.setattr(main_loop, "tick_cycle", denyut_semicoba)
    keluar = main_loop.main(
        ["--siklus", "2"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_FetchPerekam([]), jam=lambda: _ts(hour=12),
        tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert keluaran.count("Denyut penuh gagal") == 1
    assert "Denyut penuh sudah pulih" in keluaran


def test_kegagalan_polling_dicetak_ulang_setiap_20_percobaan(tmp_path, capsys):
    """Kegagalan permanen: baris pertama + cetak ulang ke-20 — bukan SUNYI
    selamanya setelah satu baris, dan bukan banjir 5.760 baris per hari."""
    conn = _fresh_db(tmp_path)
    panggilan = {"n": 0}

    def fetch(token, *, offset, timeout):
        panggilan["n"] += 1
        if panggilan["n"] == 1:
            return {"result": []}          # denyut pertama sehat
        raise ConnectionError("jaringan putus")

    keluar = main_loop.main(
        ["--siklus", "21"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: _ts(hour=12), tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    # 20 kegagalan berturut-turut (bangun 2..21): cetak di percobaan 1 dan 20.
    assert keluaran.count("Polling Telegram gagal") == 2


# --------------------------------------------------------------------------
# Review 2026-10-18 — I5: kunci instance tunggal
# (`MultipleInstances IgnoreNew` hanya menjaga instance lahir dari task;
# `python main_loop.py` / dobel-klik run_v1.bat = konsumen getUpdates KEDUA.)
# --------------------------------------------------------------------------

def test_kunci_satu_instance_menolak_pemegang_kedua(tmp_path):
    jalur = str(tmp_path / "main_loop.lock")
    pertama = main_loop._kunci_satu_instance(jalur)
    assert pertama is not None, "proses pertama harus mendapat kunci"
    try:
        kedua = main_loop._kunci_satu_instance(jalur)
        assert kedua is None, "pemegang kedua harus ditolak"
    finally:
        pertama.close()
    # Kunci dilepas OS saat handle/proses pemegang mati — tidak ada kunci basi.
    ketiga = main_loop._kunci_satu_instance(jalur)
    assert ketiga is not None, "setelah lepas, instance baru boleh masuk"
    ketiga.close()


def test_kunci_satu_instance_menolak_proses_lain(tmp_path):
    import subprocess
    import sys

    jalur = str(tmp_path / "main_loop.lock")
    pegang = main_loop._kunci_satu_instance(jalur)
    assert pegang is not None
    try:
        skrip = (
            "import sys; import main_loop; "
            "h = main_loop._kunci_satu_instance(r'" + jalur + "'); "
            "sys.exit(0 if h is not None else 1)"
        )
        hasil = subprocess.run(
            [sys.executable, "-c", skrip],
            cwd=os.path.dirname(os.path.abspath(main_loop.__file__)),
            capture_output=True, timeout=60,
        )
        assert hasil.returncode == 1, (
            "proses luar harus ditolak kunci — dua getUpdates = bencana")
    finally:
        pegang.close()
