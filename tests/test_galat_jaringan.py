"""Galat jaringan Indodax wajib menyebut sebab ASLI, bukan galat turunan.

Cacat nyata 2026-11-11 (`errors.log` dan `v1_loop.log`):

    <urlopen error [Errno 11001] getaddrinfo failed> |
    'URLError' object has no attribute 'read'

`_http_error(e)` dipanggil untuk SETIAP eksepsi, padahal hanya `HTTPError`
yang punya badan balasan (`.read()`). Pada `URLError` (DNS gagal, koneksi
diputus, timeout) panggilan itu melempar `AttributeError` yang MENUTUPI sebab
aslinya — pesan yang terbaca operator justru tentang `read`, bukan tentang
jaringan yang mati.

Tanpa jaringan, tanpa kredensial asli: `urlopen` diganti tiruan, `_creds`
disuntik nilai palsu, `sleep` dinolkan.
"""
import io
import socket
import urllib.error

import pytest

import indodax_api


def _siap(monkeypatch, galat):
    """Setiap panggilan jaringan melempar `galat`; tanpa tidur, tanpa kredensial."""
    def urlopen_palsu(req, timeout=None):
        raise galat

    monkeypatch.setattr(indodax_api.urllib.request, "urlopen", urlopen_palsu)
    monkeypatch.setattr(indodax_api, "_creds", lambda: ("KEY", "SECRET"))
    monkeypatch.setattr(indodax_api, "_WORKING", None)
    monkeypatch.setattr(indodax_api.time, "sleep", lambda s: None)


def test_urlerror_dns_menyebut_dns_bukan_read(monkeypatch):
    """RED: hari ini pesannya berbunyi "'URLError' object has no attribute 'read'"."""
    _siap(monkeypatch, urllib.error.URLError(
        socket.gaierror(11001, "getaddrinfo failed")))

    with pytest.raises(RuntimeError) as galat:
        indodax_api.account_info()

    teks = str(galat.value)
    assert "no attribute 'read'" not in teks, teks
    assert "DNS" in teks, teks


def test_urlerror_timeout_menyebut_timeout(monkeypatch):
    _siap(monkeypatch, urllib.error.URLError(socket.timeout("timed out")))

    with pytest.raises(RuntimeError) as galat:
        indodax_api._v1_call("getInfo")

    assert "timeout" in str(galat.value).lower(), str(galat.value)


def test_urlerror_koneksi_diputus_menyebut_reset(monkeypatch):
    """WinError 10054 nyata 2026-11-11 20:24: koneksi diputus bursa."""
    _siap(monkeypatch, urllib.error.URLError(
        ConnectionResetError(10054, "connection reset")))

    with pytest.raises(RuntimeError) as galat:
        indodax_api._v1_call("getInfo")

    teks = str(galat.value).lower()
    assert "diputus" in teks or "reset" in teks, teks


def test_http_error_tetap_membaca_badan_balasan(monkeypatch):
    """Hanya `HTTPError` yang punya `.read()` — badan balasannya tetap berguna."""
    def urlopen_palsu(req, timeout=None):
        # Objek baru tiap percobaan: `fp` HTTPError habis setelah dibaca.
        raise urllib.error.HTTPError(
            "https://indodax.com/tapi", 403, "Forbidden", {},
            io.BytesIO(b'{"error":"Invalid key"}'))

    monkeypatch.setattr(indodax_api.urllib.request, "urlopen", urlopen_palsu)
    monkeypatch.setattr(indodax_api, "_creds", lambda: ("KEY", "SECRET"))
    monkeypatch.setattr(indodax_api.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError) as galat:
        indodax_api._v1_call("getInfo")

    teks = str(galat.value)
    assert "HTTP 403" in teks, teks
    assert "Invalid key" in teks, teks


def test_v2_urlerror_juga_menyebut_sebab_asli(monkeypatch):
    """`_v2_get` dulu membiarkan `URLError` mentah; pesannya harus seragam."""
    _siap(monkeypatch, urllib.error.URLError(
        socket.gaierror(11001, "getaddrinfo failed")))

    with pytest.raises(RuntimeError) as galat:
        indodax_api._v2_get("/api/v2/account")

    assert "DNS" in str(galat.value), str(galat.value)


def test_pesan_galat_tidak_memuat_kunci_atau_rahasia(monkeypatch):
    """Sebab asli boleh disebut; kunci/rahasia tidak boleh ikut tercetak."""
    _siap(monkeypatch, urllib.error.URLError(
        socket.gaierror(11001, "getaddrinfo failed")))

    with pytest.raises(RuntimeError) as galat:
        indodax_api._v1_call("getInfo")

    teks = str(galat.value)
    assert "SECRET" not in teks
    assert "Sign" not in teks
