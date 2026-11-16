"""Preflight: baris "Telegram" (getMe baca-saja) dan "DNS/koneksi indodax".

Sebelum ini preflight tidak pernah memeriksa dua jalur yang paling sering
menjatuhkan bot: token Telegram mati dan jaringan/DNS putus. Keduanya
dilaporkan tiga keadaan (OK/GAGAL/TAK TERBACA) dan keluarannya tidak boleh
memuat token.

Tanpa jaringan: `cek_*` disuntik tiruan atau `requests.get`/`socket` diganti.
"""
import socket

import pytest
import requests

import main_loop
import preflight
from test_preflight import _db, _now


def _main(path, monkeypatch, now, **kwargs):
    monkeypatch.setattr(preflight.main_loop, "_offset_zona_lokal",
                        lambda _n: 7 * 3600)
    kwargs.setdefault("baca_izin", lambda: (False, False))
    kwargs.setdefault("ambil_waktu_bursa", lambda: now)
    return preflight.main(db_path=path, sekarang=lambda: now, **kwargs)


def _baris(hasil):
    return {nama: (ok, pesan) for ok, nama, pesan in hasil}


# ---------------------------------------------------------------------------
# Baris baru muncul dengan tiga keadaan
# ---------------------------------------------------------------------------

def test_baris_telegram_dan_dns_ikut_diperiksa(tmp_path, monkeypatch):
    """RED: hari ini tidak ada baris "Telegram" maupun "DNS/koneksi indodax"."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False), ambil_waktu_bursa=lambda: now,
        cek_telegram=lambda: (True, "bot @uji menjawab getMe"),
        cek_dns=lambda: (True, "DNS ok, port 443 tersambung"))

    baris = _baris(hasil)
    assert baris["Telegram"] == (True, "bot @uji menjawab getMe")
    assert baris["DNS/koneksi indodax"][0] is True


def test_telegram_gagal_dibuat_gagal_dan_no_go(tmp_path, monkeypatch, capsys):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    kode = _main(path, monkeypatch, now,
                 cek_telegram=lambda: (False, "Telegram menolak: 401 Unauthorized"),
                 cek_dns=lambda: (True, "DNS ok"))

    keluaran = capsys.readouterr().out
    assert "[GAGAL] Telegram:" in keluaran, keluaran
    assert kode == 1, "token mati harus NO-GO"


def test_cek_yang_melempar_jadi_tak_terbaca(tmp_path, monkeypatch, capsys):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    def cek_dns():
        raise OSError("resolver tidak menjawab")

    kode = _main(path, monkeypatch, now,
                 cek_telegram=lambda: (True, "bot menjawab"), cek_dns=cek_dns)

    keluaran = capsys.readouterr().out
    assert "[TAK TERBACA] DNS/koneksi indodax:" in keluaran, keluaran
    assert kode == 2, "tidak bisa dievaluasi = kode 2"


def test_keluaran_tidak_pernah_memuat_token(tmp_path, monkeypatch, capsys):
    token = "123456789:TEST-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    def cek_telegram():
        raise ConnectionError(
            f"Max retries exceeded with url: /bot{token}/getMe")

    _main(path, monkeypatch, now, cek_telegram=cek_telegram,
          cek_dns=lambda: (True, "DNS ok"))

    keluaran = capsys.readouterr().out
    assert token not in keluaran
    assert "123456789" not in keluaran
    assert "bot<disamarkan>" in keluaran, keluaran


# ---------------------------------------------------------------------------
# Pelaksana bawaan (getMe & DNS) — diganti tiruan, tanpa jaringan
# ---------------------------------------------------------------------------

class _Respons:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_get_me_menyebut_bot_tanpa_token(monkeypatch):
    monkeypatch.setattr(main_loop, "_env",
                        lambda kunci: "123456789:RAHASIA-XXXX")
    ditangkap = {}

    def get_palsu(url, timeout=None):
        ditangkap["url"] = url
        return _Respons({"ok": True, "result": {"username": "uji_bot"}})

    monkeypatch.setattr(requests, "get", get_palsu)

    ok, pesan = preflight._telegram_get_me()

    assert ok is True
    assert "uji_bot" in pesan
    assert "RAHASIA" not in pesan
    assert "/getMe" in ditangkap["url"]


def test_get_me_ditolak_telegram_jadi_gagal(monkeypatch):
    monkeypatch.setattr(main_loop, "_env", lambda kunci: "123:RAHASIA")
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Respons(
        {"ok": False, "error_code": 401, "description": "Unauthorized"}))

    ok, pesan = preflight._telegram_get_me()

    assert ok is False
    assert "401" in pesan
    assert "RAHASIA" not in pesan


def test_get_me_tanpa_token_jadi_gagal(monkeypatch):
    monkeypatch.setattr(main_loop, "_env", lambda kunci: "")

    ok, pesan = preflight._telegram_get_me()

    assert ok is False
    assert "TELEGRAM_TOKEN" in pesan


def test_dns_indodax_ok(monkeypatch):
    dipanggil = []
    monkeypatch.setattr(preflight.socket, "getaddrinfo",
                        lambda host, port: dipanggil.append((host, port)))
    monkeypatch.setattr(preflight.socket, "create_connection",
                        lambda alamat, timeout=None: _Tutup())

    ok, pesan = preflight._dns_indodax()

    assert ok is True
    assert dipanggil == [("indodax.com", 443)]
    assert "443" in pesan


def test_dns_indodax_gagal_dns_dan_gagal_port(monkeypatch):
    def getaddrinfo_gagal(host, port):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(preflight.socket, "getaddrinfo", getaddrinfo_gagal)
    ok, pesan = preflight._dns_indodax()
    assert ok is False
    assert "DNS" in pesan

    monkeypatch.setattr(preflight.socket, "getaddrinfo", lambda h, p: [])
    monkeypatch.setattr(preflight.socket, "create_connection",
                        lambda alamat, timeout=None: _Tolak())
    ok, pesan = preflight._dns_indodax()
    assert ok is False
    assert "443" in pesan


class _Tutup:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Tolak(_Tutup):
    def __enter__(self):
        raise ConnectionRefusedError(10061, "connection refused")


def test_urutan_baris_telegram_dan_dns_sebelum_mode(tmp_path, monkeypatch):
    """Dua baris baru muncul sebelum "mode" — bukan di akhir sebagai catatan."""
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False), ambil_waktu_bursa=lambda: now,
        cek_telegram=lambda: (True, "ok"), cek_dns=lambda: (True, "ok"))

    nama = [n for _ok, n, _p in hasil]
    assert "Telegram" in nama and "DNS/koneksi indodax" in nama
    assert nama.index("Telegram") < nama.index("mode")


@pytest.mark.parametrize("ok,label", [(True, "OK"), (False, "GAGAL"),
                                      (None, "TAK TERBACA")])
def test_label_tiga_keadaan(tmp_path, ok, label):
    now = _now()
    path, conn = _db(tmp_path, now=now)
    conn.close()

    if ok is None:
        def cek():
            raise RuntimeError("tidak terbaca")
    else:
        def cek():
            return (ok, "pesan")

    hasil = preflight.periksa(
        db_path=path, sekarang=lambda: now,
        baca_izin=lambda: (False, False), ambil_waktu_bursa=lambda: now,
        cek_telegram=cek, cek_dns=lambda: (True, "ok"))

    baris = _baris(hasil)
    assert baris["Telegram"][0] is ok
