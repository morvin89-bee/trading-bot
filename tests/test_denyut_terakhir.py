"""Denyut terakhir sukses: disimpan di `bot_state`, terlihat di dashboard & preflight.

Cacat nyata 2026-11-16: bot berjalan berjam-jam tanpa satu pun denyut penuh
yang berhasil (401 tiap poll), dan tidak ada satu tempat pun yang bisa
menjawab "kapan terakhir bot benar-benar bekerja?". `errors.log` penuh, tetapi
tidak ada angka tunggal — dashboard tetap tampak normal, preflight tetap GO.
"""
import sqlite3

import main_loop
import preflight
from test_main_loop import _TelegramPerekam, _fresh_db, _ts
from test_preflight import _db, _now


def _baris(hasil):
    return {nama: (ok, pesan) for ok, nama, pesan in hasil}


def _fetch_gagal(galat):
    def fetch(token, *, offset, timeout):
        raise galat
    return fetch


# ---------------------------------------------------------------------------
# Penulisan state
# ---------------------------------------------------------------------------

def test_denyut_sukses_mencatat_ts(tmp_path):
    """RED: hari ini tidak ada kolom denyut sama sekali."""
    conn = _fresh_db(tmp_path)
    jam = [_ts(hour=12)]

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        fetch=lambda token, *, offset, timeout: {"result": []},
        jam=lambda: jam[0], tidur=lambda s: None)

    baris = conn.execute(
        "SELECT denyut_ok_ts, galat_terakhir FROM bot_state WHERE id = 1"
    ).fetchone()
    assert keluar == 0
    assert baris[0] == jam[0], "denyut sukses harus mencatat waktunya"
    assert baris[1] is None, "denyut sukses menghapus galat lama"


def test_denyut_gagal_mencatat_galat_tanpa_ts(tmp_path):
    conn = _fresh_db(tmp_path)

    main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_fetch_gagal(RuntimeError("Telegram menolak: 401 Unauthorized")),
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    baris = conn.execute(
        "SELECT denyut_ok_ts, galat_terakhir FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] is None, "belum ada denyut sukses"
    assert "401" in baris[1]
    assert "123456789" not in baris[1]


def test_galat_terakhir_dipendekkan(tmp_path):
    """Pesan panjang (mis. badan HTML) tidak boleh membanjiri satu kolom."""
    conn = _fresh_db(tmp_path)

    main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_fetch_gagal(RuntimeError("x" * 1000)),
        jam=lambda: _ts(hour=12), tidur=lambda s: None)

    galat = conn.execute(
        "SELECT galat_terakhir FROM bot_state WHERE id = 1").fetchone()[0]
    assert len(galat) <= 200


def test_migrasi_kolom_denyut_idempoten(tmp_path):
    """Basis data lama (tanpa kolom baru) harus dimigrasikan, dua kali aman."""
    import dca_window

    conn = sqlite3.connect(str(tmp_path / "lama.db"))
    conn.execute(
        "CREATE TABLE bot_state ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " last_dca_date TEXT,"
        " v1_enabled INTEGER NOT NULL DEFAULT 0,"
        " shadow_day INTEGER NOT NULL DEFAULT 0,"
        " dca_sent_ts REAL, dca_status TEXT, shadow_checked_day TEXT,"
        " telegram_offset INTEGER NOT NULL DEFAULT 0,"
        " status_terakhir_hari TEXT, sapa_terakhir_ts REAL)")

    dca_window.ensure_bot_state(conn)
    dca_window.ensure_bot_state(conn)

    kolom = {b[1] for b in conn.execute("PRAGMA table_info(bot_state)")}
    assert {"denyut_ok_ts", "galat_terakhir"} <= kolom


# ---------------------------------------------------------------------------
# Preflight: basi = GAGAL, belum ada = TAK TERBACA
# ---------------------------------------------------------------------------

def test_preflight_denyut_segar_lolos(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now, denyut_ts=now - 60)
    conn.close()

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False),
        baca_izin_bursa=lambda: (False, False),
        ambil_waktu_bursa=lambda: now,
        cek_telegram=lambda: (True, "ok"), cek_dns=lambda: (True, "ok"))

    assert _baris(hasil)["denyut terakhir"][0] is True


def test_preflight_denyut_basi_jadi_gagal(tmp_path, monkeypatch):
    """RED: hari ini tidak ada pemeriksaan denyut sama sekali."""
    now = _now()
    path, conn = _db(tmp_path, now=now, denyut_ts=now - 16 * 60)
    conn.close()

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False),
        baca_izin_bursa=lambda: (False, False),
        ambil_waktu_bursa=lambda: now,
        cek_telegram=lambda: (True, "ok"), cek_dns=lambda: (True, "ok"))

    ok, pesan = _baris(hasil)["denyut terakhir"]
    assert ok is False
    assert "basi" in pesan and "16" in pesan


def test_preflight_denyut_belum_pernah_tak_terbaca(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now, denyut_ts=None)
    conn.close()

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False),
        baca_izin_bursa=lambda: (False, False),
        ambil_waktu_bursa=lambda: now,
        cek_telegram=lambda: (True, "ok"), cek_dns=lambda: (True, "ok"))

    assert _baris(hasil)["denyut terakhir"][0] is None
