"""Keamanan log bot (2026-10-20): token & denyut yang dilewatkan.

Dua masalah nyata dari `v1_loop.log`:
1. Pesan galat `requests` memuat URL lengkap — termasuk token bot — sehingga
   cetak kasarnya menulis token ke log.
2. Denyut yang dilewatkan (pembaca pasar gagal) tidak punya cap waktu di
   `errors.log`, padahal itu bahan jujur untuk hitungan G5. Baris cetak
   `v1_loop.log` tanpa timestamp tidak bisa dihitung per hari.

Tanpa jaringan, tanpa token asli.
"""
import logging
import os

import main_loop
from test_main_loop import _fresh_db, _TelegramPerekam, _ts

TOKEN_PALSU = "123456789:TEST-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
TOKEN_CONTOH = TOKEN_PALSU


def test_token_dalam_pesan_galat_disamarkan():
    pesan = (
        "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries "
        "exceeded with url: /bot" + TOKEN_CONTOH + "/getUpdates?offset=7&timeout=0"
    )
    bersih = main_loop._samarkan_pesan_galat(pesan)
    assert "123456789" not in bersih
    assert "TEST-XXXX" not in bersih
    assert "bot<disamarkan>" in bersih
    # Bagian lain tetap utuh supaya galat masih bisa didiagnosis.
    assert "getUpdates" in bersih
    assert "port=443" in bersih


def test_pesan_tanpa_token_tidak_berubah():
    pesan = "HTTPSConnectionPool(host='indodax.com', port=443): Read timed out."
    assert main_loop._samarkan_pesan_galat(pesan) == pesan


def test_denyut_dilewatkan_tercatat_bertimestamp(caplog):
    """Wajib lewat `logging` (errors.log memakai cap waktu) dan tetap bersih."""
    pesan = (
        "Pembaca pasar gagal — denyut dilewatkan, tidak ada keputusan: "
        "url /bot" + TOKEN_CONTOH + "/getUpdates"
    )
    with caplog.at_level(logging.WARNING):
        main_loop._catat_denyut_dilewatkan(pesan)
    assert caplog.records, "denyut yang dilewatkan wajib tercatat"
    teks = "\n".join(r.getMessage() for r in caplog.records)
    assert "denyut dilewatkan" in teks
    assert "123456789" not in teks


def _token_url():
    return ("/bot" + TOKEN_CONTOH + "/getUpdates?offset=0&timeout=0")


def test_token_polling_tidak_dicetak_ke_log(tmp_path, monkeypatch, capsys):
    """Cetak kasar `v1_loop.log` ikut terpengaruhi: jalur polling wajib bersih."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: None)
    panggilan = {"n": 0}

    def fetch(token, *, offset, timeout):
        panggilan["n"] += 1
        if panggilan["n"] == 2:
            raise ConnectionError(
                "Max retries exceeded with url: " + _token_url())
        return {"result": []}

    jam = [_ts(hour=12)]

    def tidur(s):
        jam[0] += main_loop.POLL_GAP_S

    keluar = main_loop.main(
        ["--siklus", "3"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)
    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert "Polling Telegram gagal" in keluaran
    assert "123456789" not in keluaran
    assert "bot<disamarkan>" in keluaran


def test_token_denyut_penuh_tidak_dicetak_ke_log(tmp_path, monkeypatch,
                                                  capsys):
    """KRITIS review 2026-10-20: jalur 'Denyut penuh gagal' mencetak `{e}`
    mentah — URL ber-token ikut tercetak ke v1_loop.log tiap outage."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: None)

    def denyut_gagal(*args, **kwargs):
        raise ConnectionError(
            "Max retries exceeded with url: " + _token_url())

    monkeypatch.setattr(main_loop, "tick_cycle", denyut_gagal)
    jam = [_ts(hour=12)]

    def tidur(s):
        jam[0] += main_loop.POLL_GAP_S

    keluar = main_loop.main(
        ["--siklus", "2"], conn=conn, telegram=_TelegramPerekam(),
        fetch=lambda token, *, offset, timeout: {"result": []},
        jam=lambda: jam[0], tidur=tidur)
    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert "Denyut penuh gagal" in keluaran
    assert "123456789" not in keluaran
    assert "bot<disamarkan>" in keluaran


def test_pembaca_gagal_juga_tercatat_bertimestamp(tmp_path, monkeypatch,
                                                  caplog):
    """Cacat nyata 2026-10-20 21:40: 'Denyut penuh gagal' tercatat di
    errors.log, tapi 'Pembaca pasar gagal — denyut dilewatkan' TIDAK walau
    tercetak ke v1_loop.log. Keduanya wajib tercatat — denyut yang dilewatkan
    adalah bahan jujur hitungan hari G5."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(main_loop, "apply_shadow_gate",
                        lambda c, *, now=None: None)
    panggilan_pembaca = {"n": 0}

    def pembaca(c):
        panggilan_pembaca["n"] += 1
        if panggilan_pembaca["n"] >= 2:
            raise RuntimeError("jaringan putus")
        return main_loop._konteks_kosong()

    dasar = _ts(hour=12)
    urutan = [dasar, dasar + 15, dasar + 300, dasar + 315]
    indeks = [0]

    def jam():
        nilai = urutan[min(indeks[0], len(urutan) - 1)]
        indeks[0] += 1
        return nilai

    with caplog.at_level(logging.WARNING):
        main_loop.main(
            ["--siklus", "4"], conn=conn, telegram=_TelegramPerekam(),
            pembaca_konteks=pembaca,
            fetch=lambda token, *, offset, timeout: {"result": []},
            jam=jam, tidur=lambda s: None)

    teks = "\n".join(r.getMessage() for r in caplog.records)
    assert "denyut dilewatkan" in teks, (
        "pembaca pasar gagal harus tercatat bertimestamp ke errors.log")


def test_errors_log_absolut_ikut_folder_modul(tmp_path, monkeypatch):
    """Cacat nyata 2026-10-20 21:40: auto-revive VBS mewarisi cwd = home,
    sehingga denyut bot jatuh ke C:\\Users\\KakiTangan\\errors.log sementara
    hitungan G5 membaca trading-bot\\errors.log. Log kegagalan wajib ABSOLUT
    ke folder modul — bukan relatif terhadap cwd yang tak diketahui."""
    monkeypatch.chdir(tmp_path)
    assert os.path.isabs(main_loop.ERRORS_LOG)
    assert os.path.dirname(main_loop.ERRORS_LOG) == os.path.dirname(
        os.path.abspath(main_loop.__file__))
