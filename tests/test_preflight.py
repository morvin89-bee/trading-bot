"""Tes preflight read-only — tanpa jaringan, tanpa menulis basis data."""
import sqlite3

import pytest

import dca_window
import pair_meta
import portfolio
import portfolio_store
import preflight
import safeguards
import shadow_log


@pytest.fixture(autouse=True)
def _cek_jaringan_palsu(monkeypatch):
    """Default preflight (getMe & DNS) tidak boleh menyentuh jaringan di tes."""
    monkeypatch.setattr(preflight, "_telegram_get_me",
                        lambda: (True, "bot @uji menjawab getMe"))
    monkeypatch.setattr(preflight, "_dns_indodax",
                        lambda: (True, "DNS ok, port 443 tersambung"))


def _now(day=1, hour=9):
    """Epoch dari tanggal/jam WIB (mesin uji = WIB, pola tests/test_dca_window)."""
    import time
    kini = time.localtime()
    return time.mktime(time.struct_time(
        (kini.tm_year, kini.tm_mon, day, hour, 0, 0, 0, 0, -1)))


_SEKARANG = object()   # penanda "denyut_ts = now" pada `_db`


def _db(tmp_path, *, now, shadow_day=7, v1=0, dca_status=None, dca_ts=None,
        meta_ts=None, denyut_ts=_SEKARANG):
    """Basis data lengkap seperti setelah bot berjalan sekali (bukan migrasi)."""
    path = tmp_path / "portfolio.db"
    conn = portfolio_store.connect(str(path))
    portfolio_store.init_db(conn)
    dca_window.ensure_bot_state(conn)
    shadow_log._ensure_table(conn)
    pair_meta._ensure_table(conn)
    ts = now if meta_ts is None else meta_ts
    for pair in portfolio.COIN_PAIRS.values():
        conn.execute(
            "INSERT OR REPLACE INTO pair_meta (pair, quantity_increment, "
            "min_base_idr, min_traded, fee_pct, fetched_ts) "
            "VALUES (?, '1e-08', 10000.0, 0.0, 0.002, ?)", (pair, ts))
    conn.execute(
        "UPDATE bot_state SET shadow_day = ?, v1_enabled = ?, "
        "denyut_ok_ts = ? WHERE id = 1",
        (shadow_day, v1, now if denyut_ts is _SEKARANG else denyut_ts))
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
    kwargs.setdefault("cek_telegram", lambda: (True, "bot @uji menjawab getMe"))
    kwargs.setdefault("cek_dns", lambda: (True, "DNS ok, port 443 tersambung"))
    return preflight.periksa(db_path=path, sekarang=lambda: now, **kwargs)


def _gagal(hasil):
    return [nama for ok, nama, _ in hasil if ok is False]


def _tidak_terbaca(hasil):
    return [nama for ok, nama, _ in hasil if ok is None]


def test_semua_lolos_berarti_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == [], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (False, False),
                          ambil_waktu_bursa=lambda: now) == 0


def test_offset_zona_bukan_wib_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()
    monkeypatch.setattr(preflight.main_loop, "_offset_zona_lokal",
                        lambda _n: 0)

    hasil = preflight.periksa(db_path=path, sekarang=lambda: now,
                              baca_izin=lambda: (False, False),
                              ambil_waktu_bursa=lambda: now)

    assert _gagal(hasil) == ["zona waktu"], hasil


def test_can_withdraw_aktif_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (False, True))

    assert _gagal(hasil) == ["izin kunci"], hasil
    pesan = next(p for ok, n, p in hasil if n == "izin kunci")
    assert "canWithdraw" in pesan


def test_izin_tidak_terbaca_exit_2(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (None, None))

    assert _tidak_terbaca(hasil) == ["izin kunci"], hasil
    assert _gagal(hasil) == [], hasil
    assert preflight.main(db_path=path, sekarang=lambda: now,
                          baca_izin=lambda: (None, None),
                          ambil_waktu_bursa=lambda: now) == 2


def test_selisih_jam_bursa_besar_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now,
                     ambil_waktu_bursa=lambda: now + 60)

    assert _gagal(hasil) == ["jam bursa"], hasil


def test_jam_bursa_gagal_dibaca_exit_2(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now)
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
    path, conn = _db(tmp_path, now=now, shadow_day=0)
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
    path, conn = _db(tmp_path, now=now, shadow_day=0, v1=1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, target="bayangan",
                     baca_izin=lambda: (True, False))

    assert _gagal(hasil) == ["mode"], hasil


def test_target_tidak_dikenal_ditolak(tmp_path, monkeypatch):
    import pytest
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    with pytest.raises(ValueError):
        _periksa(path, monkeypatch, now, target="produksi")


def test_keluaran_tidak_memuat_rahasia(tmp_path, monkeypatch, capsys):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    preflight.main(db_path=path, sekarang=lambda: now,
                   baca_izin=lambda: (False, False),
                   ambil_waktu_bursa=lambda: now)
    keluar = capsys.readouterr().out
    assert "GO" in keluar
    for terlarang in ("SECRET", "API_KEY", "api_key", "token"):
        assert terlarang not in keluar, keluar


def test_metadata_pair_basi_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now, meta_ts=now - pair_meta.MAX_AGE_S - 1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["metadata pair"], hasil
    pesan = next(p for ok, n, p in hasil if n == "metadata pair")
    assert "basi" in pesan


def test_metadata_pair_hilang_no_go(tmp_path, monkeypatch):
    """Tabel metadata belum pernah terisi = engine akan halt; jangan GO."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.execute("DELETE FROM pair_meta")
    conn.commit()
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["metadata pair"], hasil


def test_metadata_pair_di_luar_target_tidak_dituntut(tmp_path, monkeypatch):
    """BCH dipegang tetapi bukan pair target: metadatanya bukan syarat GO.

    Putusan pemilik 2026-11-15 (A): preflight hanya menuntut metadata pair
    yang benar-benar dipakai strategi. BCH tetap tampil di dashboard.
    """
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.execute("DELETE FROM pair_meta WHERE pair = 'bch_idr'")
    conn.commit()
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == [], hasil
    pesan = next(p for ok, nama, p in hasil if nama == "metadata pair")
    assert "bch_idr" not in pesan


def test_pair_di_luar_target_dilaporkan_sebagai_informasi(tmp_path, monkeypatch):
    """BCH yang dipegang disebut sebagai INFORMASI, bukan alasan NO-GO."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.execute(
        "INSERT INTO trades (pair, side, price, qty, quote, fee, ts) "
        "VALUES ('bch_idr', 'buy', 1000.0, 0.01, 10.0, 0.0, ?)", (now,))
    conn.commit()
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == [], hasil
    baris = [p for ok, nama, p in hasil if nama == "di luar target"]
    assert len(baris) == 1, hasil
    assert "bch_idr" in baris[0]


def test_tanpa_posisi_di_luar_target_informasi_jujur(tmp_path, monkeypatch):
    """Tanpa posisi di luar target, baris informasi tetap ada dan jujur."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    baris = [p for ok, nama, p in hasil if nama == "di luar target"]
    assert baris == ["tidak ada posisi di luar target"], hasil


def test_tabel_audit_sakelar_belum_ada_no_go(tmp_path, monkeypatch):
    """Tanpa `bot_state_audit`, penulisan sakelar resmi akan gagal — NO-GO."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.execute("DROP TABLE bot_state_audit")
    conn.commit()
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert "db" in _gagal(hasil), hasil
    pesan = [p for ok, nama, p in hasil if nama == "db"]
    assert any("bot_state_audit" in p for p in pesan), pesan


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
    path, conn = _db(tmp_path, now=now, dca_status="awaiting", dca_ts=kirim)
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
    path, conn = _db(tmp_path, now=now, dca_status="aneh", dca_ts=now)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["jendela DCA"], hasil


def test_bukti_bayangan_belum_cukup_no_go(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now, shadow_day=2)
    conn.close()

    hasil = _periksa(path, monkeypatch, now)

    assert _gagal(hasil) == ["mode"], hasil
    pesan = next(p for ok, n, p in hasil if n == "mode")
    assert "2 dari lantai 7" in pesan


def test_sakelar_nyala_tidak_menuntut_hari_bayangan(tmp_path, monkeypatch):
    now = _now()
    path, conn = _db(tmp_path, now=now, shadow_day=0, v1=1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, baca_izin=lambda: (True, False))

    assert _gagal(hasil) == [], hasil


# ---------------------------------------------------------------------------
# Matriks izin x sakelar (butir 1). Tabel hasil yang sama ada di
# docs/GOLIVE_CHECKLIST.md — ubah keduanya bersamaan.
# ---------------------------------------------------------------------------
IZIN = (True, False, None)


def _harapan_izin(can_trade, can_withdraw, v1):
    """Hasil yang diharapkan: "OK" | "GAGAL" | "TAK TERBACA".

    Aturan §6.6 ditulis ulang sebagai tabel, bukan dengan memanggil fungsinya:
    tes harus gagal bila aturannya berubah diam-diam.
    """
    if can_trade is None or can_withdraw is None:
        return "TAK TERBACA"
    if can_withdraw:
        return "GAGAL"
    return "OK" if can_trade == bool(v1) else "GAGAL"


@pytest.mark.parametrize("can_trade", IZIN)
@pytest.mark.parametrize("can_withdraw", IZIN)
@pytest.mark.parametrize("v1", (0, 1))
def test_permissions_ok_matriks_izin_dan_sakelar(can_trade, can_withdraw, v1):
    # `permissions_ok` tidak punya keadaan TAK TERBACA: ragu = halt.
    harap = _harapan_izin(can_trade, can_withdraw, v1)
    alasan = safeguards.permissions_ok(
        can_trade, can_withdraw, switch_on=bool(v1))
    assert ("OK" if alasan is None else "GAGAL") == (
        "OK" if harap == "OK" else "GAGAL"), alasan


@pytest.mark.parametrize("target", preflight.TARGET)
@pytest.mark.parametrize("can_trade", IZIN)
@pytest.mark.parametrize("can_withdraw", IZIN)
@pytest.mark.parametrize("v1", (0, 1))
def test_preflight_matriks_izin_dan_sakelar(tmp_path, monkeypatch, target,
                                            can_trade, can_withdraw, v1):
    """Preflight menilai izin TEPAT seperti `permissions_ok` menilai order.

    `shadow_day=7` supaya pemeriksaan `mode` target live tidak ikut gagal;
    yang diuji di sini hanya izin x sakelar.
    """
    now = _now()
    path, conn = _db(tmp_path, now=now, v1=v1)
    conn.close()

    hasil = _periksa(path, monkeypatch, now, target=target,
                     baca_izin=lambda: (can_trade, can_withdraw))

    harap = _harapan_izin(can_trade, can_withdraw, v1)
    # Target bayangan menolak sakelar yang sudah nyala — satu-satunya
    # pemeriksaan lain yang boleh gagal pada matriks ini.
    mode = {"mode"} if (target == "bayangan" and v1) else set()
    gagal = set(_gagal(hasil))
    if harap == "TAK TERBACA":
        assert _tidak_terbaca(hasil) == ["izin kunci"], hasil
        assert gagal == mode, hasil
    else:
        assert _tidak_terbaca(hasil) == [], hasil
        assert gagal == mode | ({"izin kunci"} if harap == "GAGAL" else set()), (
            hasil)


def test_urutan_golive_preflight_di_tiap_langkah(tmp_path, monkeypatch):
    """Hasil preflight pada tiap keadaan urutan §0 checklist.

    Keadaan awal: bot mati, kunci baca-saja (`canTrade` False), sakelar 0.
    Temuan: memasang kunci trade SEBELUM sakelar menyala memaksa preflight
    GAGAL — itu disengaja (`permissions_ok` menuntut kunci dan sakelar
    sejalan), jadi urutannya yang harus kunci + sakelar bersamaan.
    """
    now = _now()
    path, conn = _db(tmp_path, now=now)          # shadow_day=7
    conn.close()

    langkah = [
        ("awal: kunci baca-saja, sakelar 0", False, 0, set(), set()),
        ("kunci trade dipasang, sakelar masih 0", True, 0,
         {"izin kunci"}, {"izin kunci"}),
        ("kunci trade + sakelar nyala bersamaan", True, 1, set(), {"mode"}),
        ("dibatalkan: set_shadow_day(0), kunci baca-saja", False, 0,
         set(), set()),
    ]
    for nama, can_trade, v1, gagal_live, gagal_bayangan in langkah:
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE bot_state SET v1_enabled = ? WHERE id = 1",
                     (v1,))
        conn.commit()
        conn.close()
        izin = lambda ct=can_trade: (ct, False)   # noqa: E731
        live = _periksa(path, monkeypatch, now, target="live",
                        baca_izin=izin)
        bayangan = _periksa(path, monkeypatch, now, target="bayangan",
                            baca_izin=izin)
        assert set(_gagal(live)) == gagal_live, (nama, live)
        assert set(_gagal(bayangan)) == gagal_bayangan, (nama, bayangan)
