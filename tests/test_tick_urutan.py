"""Urutan `tick_cycle` saat polling gagal dan saat jam tenang (DND).

Laporan review 2026-11-16 (butir 4) menyimpulkan bahwa kegagalan langkah 4
(polling Telegram) membatalkan langkah 5-7, dan jam tenang membatalkan
SELURUH denyut. Tes ini mengunci perilaku itu apa adanya: usulan "langkah
baca-saja tetap jalan" harus jadi keputusan pemilik, bukan perubahan diam-diam.
"""
import main_loop
from test_main_loop import _TelegramPerekam, _fresh_db, _satu_siklus, _ts


def _catat(monkeypatch, jejak):
    """Ganti langkah 5-7 dengan pencatat; kembalikan tanpa efek samping."""
    monkeypatch.setattr(
        main_loop.dca_window, "should_remind",
        lambda conn, now: jejak.append("pengingat") and False)
    monkeypatch.setattr(
        main_loop.engine, "tick",
        lambda *a, **k: jejak.append("engine") or [])
    monkeypatch.setattr(
        main_loop, "apply_shadow_gate",
        lambda conn, *, now=None: jejak.append("shadow"))


def test_polling_gagal_membatalkan_langkah_5_sampai_7(tmp_path, monkeypatch):
    main_loop.reset_state()
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(main_loop.pair_meta, "refresh",
                        lambda c, *, now: None)
    jejak = []
    _catat(monkeypatch, jejak)

    def fetch(token, *, offset, timeout):
        raise RuntimeError("Telegram menolak: 401 Unauthorized")

    try:
        _satu_siklus(conn, fetch=fetch)
    except RuntimeError:
        pass
    else:
        raise AssertionError("galat polling harus naik ke penjaga denyut")

    assert jejak == [], (
        "langkah 5-7 (pengingat, engine, sakelar bayangan) TIDAK berjalan "
        f"saat polling gagal — hari bayangan tidak maju: {jejak}")


def test_jam_tenang_tidak_menjalankan_denyut_apa_pun(tmp_path, monkeypatch):
    """DND 23.00-07.00: tidak ada polling, metadata, atau denyut penuh."""
    conn = _fresh_db(tmp_path)
    jejak = []

    monkeypatch.setattr(main_loop.pair_meta, "refresh",
                        lambda c, *, now: jejak.append("metadata"))

    def fetch(token, *, offset, timeout):
        jejak.append("polling")
        return {"result": []}

    jam = [_ts(hour=2)]

    def tidur(s):
        jam[0] += s

    keluar = main_loop.main(
        ["--siklus", "2"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)

    assert keluar == 0
    assert jejak == [], f"jam tenang tidak boleh bekerja: {jejak}"
    baris = conn.execute(
        "SELECT denyut_ok_ts FROM bot_state WHERE id = 1").fetchone()
    assert baris[0] is None, (
        "jam tenang tidak menghasilkan denyut sukses; dashboard/preflight "
        "wajib mengecualikan DND, bukan mencatat sehat palsu")
