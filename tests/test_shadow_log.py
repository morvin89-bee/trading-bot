"""Tes mode bayangan — tidak pernah membuka jaringan."""
import os
import sqlite3
import tempfile

import proposal
import rules
import shadow_log


def _fresh_db(tmp_path):
    """Buat koneksi ke basis data baru dan kosong."""
    db_path = os.path.join(tmp_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _act(kind="BUY", pair="btc_idr", amount=275000, price=1000.0):
    return rules.Action(kind, pair, amount, amount / price, price, "alasan uji", True)


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM proposals ORDER BY id")]


def test_records_every_proposal_with_its_rule(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="Take-profit +50%", reserve_after=0,
                              window_s=90, now=0.0)
    shadow_log.record(conn, p, "proposed")
    assert [r["rule"] for r in _rows(conn)] == ["Take-profit +50%"]


def test_summary_lists_each_outcome_in_indonesian(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="DCA bulanan", reserve_after=0,
                              window_s=86400, now=0.0)
    shadow_log.record(conn, p, "expired")
    lines = shadow_log.daily_summary(conn, "1970-01-01")
    assert len(lines) == 1 and "kedaluarsa" in lines[0]


def test_state_take_profit_menyimpan_target_dan_terjual_per_level(tmp_path):
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.10,
        ts=1.0, mode="shadow", hari="2026-11-07")
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.15,
        ts=2.0, mode="shadow", hari="2026-11-07")
    assert shadow_log.take_profit_state(conn, mode="shadow") == {
        ("btc_idr", 50.0): (0.25, 0.25, "2026-11-07")}


def test_state_take_profit_tidak_hilang_saat_basis_berubah(tmp_path):
    """Dulu basis avg_cost yang berubah mengganti seluruh baris. Sekarang
    level yang sudah dieksekusi tetap tercatat; reset hanya saat posisi tutup."""
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.25,
        ts=1.0, mode="shadow", hari="2026-11-06")
    shadow_log.catat_take_profit(
        conn, "btc_idr", 100, qty_target=0.25, qty=0.25,
        ts=2.0, mode="shadow", hari="2026-11-07")
    assert shadow_log.take_profit_state(conn, mode="shadow") == {
        ("btc_idr", 50.0): (0.25, 0.25, "2026-11-06"),
        ("btc_idr", 100.0): (0.25, 0.25, "2026-11-07"),
    }


def test_state_take_profit_dibuang_saat_posisi_tertutup(tmp_path):
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.25,
        ts=1.0, mode="shadow", hari="2026-11-06")
    assert shadow_log.take_profit_state(
        conn, pairs=set(), mode="shadow") == {}
    assert conn.execute(
        "SELECT COUNT(*) FROM take_profit_state").fetchone()[0] == 0


def test_state_take_profit_terpisah_per_mode(tmp_path):
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.25,
        ts=1.0, mode="shadow", hari="2026-11-06")
    shadow_log.catat_take_profit(
        conn, "btc_idr", 100, qty_target=0.25, qty=0.25,
        ts=2.0, mode="live", hari="2026-11-06")
    assert shadow_log.take_profit_state(conn, mode="shadow") == {
        ("btc_idr", 50.0): (0.25, 0.25, "2026-11-06")}
    assert shadow_log.take_profit_state(conn, mode="live") == {
        ("btc_idr", 100.0): (0.25, 0.25, "2026-11-06")}


def test_hapus_mode_shadow_untuk_aktifkan_live(tmp_path):
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.25,
        ts=1.0, mode="shadow", hari="2026-11-06")
    shadow_log.catat_take_profit(
        conn, "btc_idr", 100, qty_target=0.25, qty=0.25,
        ts=2.0, mode="live", hari="2026-11-06")
    shadow_log.hapus_take_profit_mode(conn, "shadow")
    assert shadow_log.take_profit_state(conn, mode="shadow") == {}
    assert shadow_log.take_profit_state(conn, mode="live") == {
        ("btc_idr", 100.0): (0.25, 0.25, "2026-11-06")}


def test_hapus_catatan_take_profit_mengembalikan_sisa(tmp_path):
    conn = _fresh_db(tmp_path)
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.10,
        ts=1.0, mode="live", hari="2026-11-07")
    shadow_log.catat_take_profit(
        conn, "btc_idr", 50, qty_target=0.25, qty=0.15,
        ts=2.0, mode="live", hari="2026-11-07")
    shadow_log.hapus_take_profit(conn, "btc_idr", 50, qty=0.10, mode="live")
    assert shadow_log.take_profit_state(conn, mode="live") == {
        ("btc_idr", 50.0): (0.25, 0.15, "2026-11-07")}
    shadow_log.hapus_take_profit(conn, "btc_idr", 50, qty=0.15, mode="live")
    assert shadow_log.take_profit_state(conn, mode="live") == {}


def test_migrasi_tabel_lama_tetap_mengingat_ambang(tmp_path):
    """Tabel lama (pair, avg_cost, level) dimigrasi; level dianggap selesai
    di kedua mode supaya tidak ada penjualan dobel setelah pembaruan."""
    conn = _fresh_db(tmp_path)
    conn.execute(
        "CREATE TABLE take_profit_state ("
        "pair TEXT PRIMARY KEY, avg_cost REAL NOT NULL, "
        "level REAL NOT NULL, updated_ts REAL)")
    conn.execute(
        "INSERT INTO take_profit_state VALUES ('btc_idr', 1000.0, 50.0, 1.0)")
    conn.commit()
    assert shadow_log.take_profit_state(conn, mode="shadow") == {
        ("btc_idr", 50.0): (0.0, 0.0, None)}
    assert shadow_log.take_profit_state(conn, mode="live") == {
        ("btc_idr", 50.0): (0.0, 0.0, None)}


def test_record_dan_pending_menyimpan_revisi(tmp_path):
    """Revisi re-quote harus pulih setelah restart; pesan lama tetap ditolak."""
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="DCA bulanan", reserve_after=0,
                              window_s=86400, now=0.0)
    shadow_log.record(conn, p, "proposed")
    p.revisi = 2
    shadow_log.perbarui_harga(conn, p, "Re-quote DCA")
    pulih = shadow_log.pending(conn, 1.0)
    assert len(pulih) == 1
    assert pulih[0].revisi == 2


def test_rejected_count_is_the_shadow_reset_signal(tmp_path):
    conn = _fresh_db(tmp_path)
    for i, outcome in enumerate(("gate_rejected", "spread_rejected")):
        p = proposal.new_proposal(_act(), rule="r", reserve_after=0,
                                  window_s=90, now=float(i))
        shadow_log.record(conn, p, outcome)
    assert shadow_log.rejected_count(conn, "1970-01-01") == 2


def test_rejected_count_tidak_menghitung_batal_atau_proposed(tmp_path):
    """Hanya penolakan gerbang/spread yang mereset shadow_day.

    Batal dan proposed adalah keputusan pengguna/antrean biasa — bukan
    penolakan sistem — jadi tidak boleh memperpanjang periode bayangan.
    """
    conn = _fresh_db(tmp_path)
    for i, outcome in enumerate(("batal", "proposed", "tunda", "setuju")):
        p = proposal.new_proposal(_act(), rule="r", reserve_after=0,
                                  window_s=90, now=float(i))
        shadow_log.record(conn, p, outcome)
    assert shadow_log.rejected_count(conn, "1970-01-01") == 0
