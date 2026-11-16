"""Tanda tangan TAPI Indodax: timestamp harus dihitung SAAT KIRIM, per percobaan.

Cacat nyata (item review 2026-11-16): `_v1_call` menyiapkan `timestamp`,
badan, tanda tangan, dan objek `Request` SEKALI di depan, lalu mengulang
percobaan yang sama hingga tiga kali dengan jeda 1 + 2 detik. Percobaan
ketiga membawa timestamp yang sudah berumur ~3-4 detik — di jalur lambat itu
cukup untuk "Invalid timestamp ... outside of the recvWindow".

Tes ini tidak membuka jaringan: `urlopen`, `_creds`, dan jam disuntikkan.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.parse

import pytest

import indodax_api


class _Jam:
    """Jam tiruan: setiap `time()` maju satu detik, `sleep` tidak menunggu."""

    def __init__(self, mulai=1_700_000_000.0):
        self.detik = mulai

    def time(self):
        self.detik += 1.0
        return self.detik

    def sleep(self, _s):
        pass


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({"success": 1}).encode()


def _pasang(monkeypatch, *, gagal=2):
    """`urlopen` palsu: `gagal` percobaan pertama melempar URLError."""
    kiriman = []

    def urlopen(req, timeout=30):
        kiriman.append(req)
        if len(kiriman) <= gagal:
            raise urllib.error.URLError("timeout")
        return _Resp()

    monkeypatch.setattr(indodax_api, "_creds", lambda: ("kunci", "rahasia"))
    monkeypatch.setattr(indodax_api, "time", _Jam())
    monkeypatch.setattr(indodax_api.urllib.request, "urlopen", urlopen)
    return kiriman


def _timestamp(req):
    return int(urllib.parse.parse_qs(req.data.decode())["timestamp"][0])


def test_v1_timestamp_dihitung_ulang_tiap_percobaan(monkeypatch):
    """RED: hari ini ketiga percobaan membawa timestamp yang SAMA."""
    kiriman = _pasang(monkeypatch, gagal=2)

    assert indodax_api._v1_call("getInfo") == {"success": 1}

    assert len(kiriman) == 3
    cap = [_timestamp(r) for r in kiriman]
    assert cap == sorted(cap) and len(set(cap)) == 3, (
        f"timestamp harus maju tiap percobaan, bukan dibekukan: {cap}")


def test_v1_tanda_tangan_cocok_dengan_badan_yang_dikirim(monkeypatch):
    """Tanda tangan wajib dihitung dari badan (timestamp) percobaan itu."""
    kiriman = _pasang(monkeypatch, gagal=1)

    indodax_api._v1_call("getInfo")

    for req in kiriman:
        harapan = hmac.new(b"rahasia", req.data, hashlib.sha512).hexdigest()
        assert req.get_header("Sign") == harapan


def test_v1_gagal_total_tetap_menyebut_sebab(monkeypatch):
    _pasang(monkeypatch, gagal=99)

    with pytest.raises(RuntimeError, match="timeout"):
        indodax_api._v1_call("getInfo")


def test_v2_memakai_recvwindow_15_detik_dan_timestamp_saat_kirim(monkeypatch):
    """Nilai recvWindow v2 terdokumentasi di sini: 15000 ms."""
    kiriman = _pasang(monkeypatch, gagal=0)

    indodax_api._v2_get("/api/v2/account", {"foo": "bar"})

    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(kiriman[0].full_url).query)
    assert query["recvWindow"] == ["15000"]
    assert int(query["timestamp"][0]) == 1_700_000_001_000
