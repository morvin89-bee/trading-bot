"""Tes preflight read-only — tanpa jaringan, tanpa menulis basis data."""
import sqlite3

import dca_window
import portfolio_store
import preflight
import shadow_log


def _now(day=1, hour=9):
    """Epoch dari tanggal/jam WIB (mesin uji = WIB, pola tests/test_dca_window)."""
    import time
    kini = time.localtime()
    return time.mktime(time.struct_time(
        (kini.tm_year, kini.tm_mon, day, hour, 0, 0, 0, 0, -1)))


def _db(tmp_path, *, shadow_day=7, v1=0, dca_status=None, dca_ts=None):
    """Basis data lengkap seperti setelah bot berjalan sekali (bukan migrasi)."""
    path = tmp_path / "portfolio.db"
    conn = portfolio_store.connect(str(path))
    portfolio_store.init_db(conn)
    dca_window.ensure_bot_state(conn)
    shadow_log._ensure_table(conn)
    conn.execute(
        "UPDATE bot_state SET shadow_day = ?, v1_enabled = ? WHERE id = 1",
        (shadow_day, v1))
    if dca_status is not None:
        conn.execute(
            "UPDATE bot_state SET last_dca_date = ?, dca_status = ?, "
            "dca_sent_ts = ? WHERE id = 1",
            (dca_window._bulan(dca_ts), dca_status, dca_ts))
    conn.commit()
    return path, conn


def _periksa(path, monkeypatch, now, **kwargs):
    monkeypatch.setattr(preflight.main_loop, "_offset_zona_lokal",
                        lambda _n: 7 * 3600)
    kwargs.setdefault("baca_izin", lambda: (False, False))
    kwargs.setdefault("ambil_waktu_bursa", lambda: now)
    return preflight.periksa(db_path=path, sekarang=lambda: now, **kwargs)


def _gagal(hasil):
    return [nama for ok, nama, _ in hasil if ok is False]


def _tidak_terbaca(hasil):
    return [nama for ok, nama, _ in hasil if ok is None]


def test_semua_lolos_berarti_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == [], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=lambda: now) == 0


def test_offset_zona_bukan_wib_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()
    monkeypatch.setattr(preflight.main_loop, "_offset_zona_lokal",
                        lambda _n: 0)

    hasil = preflight.periksa(db_path=path, sekarang=lambda: now,
                              baca_izin=lambda: (False, False),
                              ambil_waktu_bursa=lambda: now)

    assert _gagal(hasil) == ["zona waktu"], hasil


def test_can_withdraw_aktif_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (False, True))

    assert _gagal(hasil) == ["izin kunci"], hasil
    pesan = next(p for ok, n, p in hasil if n == "izin kunci")
    assert "canWithdraw" in pesan


def test_izin_tidak_terbaca_exit_2(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (None, None))

    assert _tidak_terbaca(hasil) == ["izin kunci"], hasil
    assert _gagal(hasil) == [], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (None, None),
                          ambil_waktu_bursa=lambda: now) == 2


def test_selisih_jam_bursa_besar_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    hasil = _periksa(path, monkeypatch, now,
                     ambil_waktu_bursa=lambda: now + 60)

    assert _gagal(hasil) == ["jam bursa"], hasil


def test_jam_bursa_gagal_dibaca_exit_2(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    def rusak():
        raise RuntimeError("jaringan putus")

    hasil = _periksa(path, monkeypatch, now, ambil_waktu_bursa=rusak)

    assert _tidak_terbaca(hasil) == ["jam bursa"], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=rusak) == 2


def test_db_belum_ada_exit_2_dan_tidak_dibuat(tmp_path, monkeypatch):
    now = _now()
    path = tmp_path / "belum_ada.db"

    hasil = _periksa(path, monkeypatch, now)

    assert "db" in _tidak_terbaca(hasil), hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=lambda: now) == 2
    assert not path.exists(), "preflight tidak boleh membuat basis data"


def test_gagal_menang_atas_tak_terbaca(tmp_path, monkeypatch):
    """Ada satu kegagalan yang bisa dievaluasi -> NO-GO (1), bukan 2."""
    now = _now()
    path = tmp_path / "belum_ada.db"

    def rusak():
        raise RuntimeError("jaringan putus")

    kode = preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, True),
                          ambil_waktu_bursa=rusak)

    assert kode == 1


def test_target_bayangan_tidak_menuntut_hari_bayangan(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, shadow_day=0)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, target="bayangan")
    assert _gagal(hasil) == [] and _tidak_terbaca(hasil) == [], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=lambda: now,
                          target="bayangan") == 0

    # Target live tetap menuntut lantai hari bayangan (default = live).
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=lambda: now) == 1


def test_target_bayangan_menolak_sakelar_eksekusi_nyala(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, shadow_day=0, v1=1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, target="bayangan",
                     baca_izin=lambda: (True, False))

    assert _gagal(hasil) == ["mode"], hasil


def test_target_tidak_dikenal_ditolak(tmp_path, monkeypatch):
    import pytest
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    with pytest.raises(ValueError):
        _periksa(path, monkeypatch, now, target="produksi")


def test_keluaran_tidak_memuat_rahasia(tmp_path, monkeypatch, capsys):
    now = _now()
    path, conn = _db(tmp_path)
    conn.close()

    preflight.main(db_path=path, sekarang=lambda: now,
                   baca_izin=lambda: (False, False),
                   ambil_waktu_bursa=lambda: now)
    keluar = capsys.readouterr().out
    assert "GO" in keluar
    for terlarang in ("SECRET", "API_KEY", "api_key", "token"):
        assert terlarang not in keluar, keluar


def test_migrasi_bot_state_belum_diterapkan_no_go(tmp_path, monkeypatch):
    now = _now()
    path = tmp_path / "lama.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE bot_state (id INTEGER PRIMARY KEY, last_dca_date TEXT, "
        "v1_enabled INTEGER, shadow_day INTEGER, dca_sent_ts REAL, "
        "dca_status TEXT)")
    conn.execute("INSERT INTO bot_state (id) VALUES (1)")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    gagal = _gagal(hasil)
    assert "migrasi bot_state" in gagal, hasil
    assert "migrasi proposals" in gagal, hasil


def test_jendela_dca_lewat_tidak_menulis_missed(tmp_path, monkeypatch):
    """Inti read-only: `dca_state` akan menulis `mark_missed`, preflight tidak."""
    # Tanggal 15 supaya "25 jam lalu" tetap di bulan yang sama.
    now = _now(day=15)
    kirim = now - 25 * 3600
    path, conn = _db(tmp_path, dca_status="awaiting", dca_ts=kirim)
    bulan = dca_window._bulan(kirim)

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == [], hasil
    pesan = next(p for ok, n, p in hasil if n == "jendela DCA")
    assert "lewat" in pesan
    baris = conn.execute(
        "SELECT dca_status FROM bot_state WHERE id = 1").fetchone()
    assert baris[0] == "awaiting", "preflight tidak boleh mengubah status DCA"
    tersisa = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE id = ?",
        (f"dca_missed_{bulan}",)).fetchone()[0]
    assert tersisa == 0, "preflight tidak boleh mencatat dca_missed"
    conn.close()


def test_dca_status_rusak_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, dca_status="aneh", dca_ts=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["jendela DCA"], hasil


def test_bukti_bayangan_belum_cukup_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, shadow_day=2)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["mode"], hasil
    pesan = next(p for ok, n, p in hasil if n == "mode")
    assert "2 dari lantai 7" in pesan


def test_sakelar_nyala_tidak_menuntut_hari_bayangan(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, shadow_day=0, v1=1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (True, False))

    assert _gagal(hasil) == [], hasil
