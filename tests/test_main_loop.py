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
    jadi jalur Sasha benar-benar berjalan tanpa menyentuh bursa.
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
    """`mark_batal` menolak (False) → pengguna diberi tahu (kabel Task 13 #8)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))
    dca_window.mark_confirmed(conn, _ts(hour=10))

    fetch = _FetchPerekam([_update_tombol("batal", "usulan-tua")])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx_kosong(), fetch=fetch)

    assert any("Bulan ini sudah selesai" in b for b in telegram.lines)


def test_jawaban_setuju_menandai_dca_bulan_ini_confirmed(tmp_path):
    """Tanpa `mark_confirmed`, pengingat tetap menyala dan +24 jam tercipta
    catatan `dca_missed` palsu (kabel Task 13 #7)."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    dca_window.mark_pending(conn, _ts(hour=9))

    fetch = _FetchPerekam([_update_tombol("setuju", "dca_bulan_ini")])
    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12),
                 ctx=_ctx_kosong(), fetch=fetch)

    assert dca_window.dca_state(conn, _ts(hour=12))[0] == "confirmed"


def test_perintah_ulang_mengusulkan_langsung_dengan_data_baru(tmp_path):
    """`/ulang` memicu engine.tick SEKARANG, bukan menunggu denyut berikutnya."""
    conn = _fresh_db(tmp_path)
    telegram = _TelegramPerekam()
    fetch = _FetchPerekam([_update_teks("/ulang")])

    _satu_siklus(conn, telegram=telegram, now=_ts(hour=12, minute=2),
                 ctx=_ctx(), fetch=fetch)

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
    assert tidur == [main_loop.HEARTBEAT_S]


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