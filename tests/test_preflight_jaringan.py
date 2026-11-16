"""Preflight: baris Telegram/DNS tanpa token, 401 = GAGAL (bukan TAK TERBACA).

Cacat 2026-11-16: `_telegram_get_me` memanggil `raise_for_status()` SEBELUM
membaca badan JSON. Telegram menjawab 401 dengan badan JSON
(`{"ok": false, "error_code": 401, ...}`), jadi token mati berubah menjadi
"tidak terbaca" — preflight melaporkan TAK TERBACA padahal jawabannya jelas.
"""
import requests

import preflight
from test_preflight import _db, _now, _periksa


class _Resp:
    """Respons tiruan `requests.get` — tanpa jaringan."""

    def __init__(self, kode, data):
        self.status_code = kode
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error for url: "
                "https://api.telegram.org/bot123456:RAHASIA/getMe")

    def json(self):
        if self._data is None:
            raise ValueError("badan bukan JSON")
        return self._data


def _telegram(monkeypatch, resp):
    monkeypatch.setattr(preflight.main_loop, "_env",
                        lambda nama: "123456:RAHASIA")
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)


def _baris(hasil, nama):
    return next(b for b in hasil if b[1] == nama)


def test_getme_401_gagal_bukan_tak_terbaca(tmp_path, monkeypatch):
    """RED: hari ini 401 menjadi TAK TERBACA karena raise_for_status didahulukan."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()
    _telegram(monkeypatch, _Resp(401, {
        "ok": False, "error_code": 401, "description": "Unauthorized"}))

    hasil = _periksa(path, monkeypatch, now,
                     cek_telegram=preflight._telegram_get_me)

    ok, _nama, pesan = _baris(hasil, "Telegram")
    assert ok is False, hasil
    assert "401" in pesan
    assert "RAHASIA" not in pesan


def test_getme_403_gagal_dan_tanpa_token(monkeypatch):
    _telegram(monkeypatch, _Resp(403, {
        "ok": False, "error_code": 403,
        "description": "Forbidden: bot was blocked by the user"}))

    ok, pesan = preflight._telegram_get_me()

    assert ok is False
    assert "403" in pesan
    assert "RAHASIA" not in pesan


def test_getme_token_kosong_gagal_tanpa_jaringan(monkeypatch):
    def get(*a, **k):
        raise AssertionError("token kosong tidak boleh membuka jaringan")

    monkeypatch.setattr(preflight.main_loop, "_env", lambda nama: "")
    monkeypatch.setattr(requests, "get", get)

    ok, pesan = preflight._telegram_get_me()

    assert ok is False
    assert "TELEGRAM_TOKEN" in pesan


def test_dns_gagal_jadi_gagal_bukan_tak_terbaca(monkeypatch):
    """DNS/port gagal = bisa dievaluasi (GAGAL), bukan TAK TERBACA."""
    import socket

    def getaddrinfo(*a, **k):
        raise OSError("nama tidak dikenal")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    ok, pesan = preflight._dns_indodax()

    assert ok is False
    assert "DNS gagal" in pesan
