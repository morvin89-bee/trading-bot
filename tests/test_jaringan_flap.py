"""Dua penolakan nyata 2026-10-20, keduanya terukur.

1. Telegram: latensi api.telegram.org dari mesin ini berayun 4,7-5,0 dtk
   (lolos) sampai 11,6 dtk (putus) pada garis read-timeout 10 dtk —
   menghasilkan flap 119 gagal / 119 pulih dalam 5,5 jam.
2. Indodax v2: dua kali `HTTP 400: Invalid timestamp ... recvWindow`
   meski geseran jam mesin terukur hanya 0,8 dtk; payload tidak membawa
   recvWindow eksplisit sehingga batas 15 dtk server berlaku mutlak.

Semua tes TANPA jaringan dan TANPA kredensial: `urlopen`/`get` diganti
tiruan, `_creds` disuntik nilai palsu.
"""
import json


def test_live_fetch_timeout_minimal_20_detik():
    """Garis 10 dtk terlalu rapat terhadap latensi terukur 11,6 dtk."""
    import telegram_gate

    dicatat = {}

    class _Respons:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "result": []}

    def get_palsu(url, params=None, timeout=None):
        dicatat["timeout"] = timeout
        return _Respons()

    telegram_gate.live_fetch("TOKEN", offset=0, timeout=0, get=get_palsu)
    assert dicatat["timeout"] >= 20, (
        "poll sehat berlatensi 11,6 dtk akan diputus di garis 10 dtk")

    # Pemanggil boleh meminta lebih besar; jangan pernah diturunkan.
    telegram_gate.live_fetch("TOKEN", offset=0, timeout=30, get=get_palsu)
    assert dicatat["timeout"] >= 30


def test_v2_get_membawa_recvwindow_eksplisit(monkeypatch):
    """Timestamp v2 ditolak bila permintaan tertahan di jalur lambat."""
    import indodax_api

    monkeypatch.setattr(indodax_api, "_creds", lambda: ("KEY", "SECRET"))
    ditangkap = {}

    class _Resp:
        def read(self):
            return b'{"success": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen_palsu(req, timeout=None):
        ditangkap["url"] = req.full_url
        ditangkap["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(indodax_api.urllib.request, "urlopen", urlopen_palsu)

    indodax_api._v2_get("/api/v2/account")

    assert "recvWindow=15000" in ditangkap["url"], (
        "payload v2 wajib membawa recvWindow eksplisit 15000 ms")


def test_v2_get_tanda_tangan_mencakup_recvwindow(monkeypatch):
    """recvWindow harus ikut ditandatangani, bukan ditempel setelahnya."""
    import urllib.parse

    import indodax_api

    monkeypatch.setattr(indodax_api, "_creds", lambda: ("KEY", "SECRET"))
    ditangkap = {}

    class _Resp:
        def read(self):
            return b'{"success": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen_palsu(req, timeout=None):
        ditangkap["query"] = req.full_url.split("?", 1)[1]
        ditangkap["sign"] = req.headers.get("Sign")
        return _Resp()

    monkeypatch.setattr(indodax_api.urllib.request, "urlopen", urlopen_palsu)

    indodax_api._v2_get("/api/v2/account")

    query = urllib.parse.unquote(ditangkap["query"])
    import hashlib
    import hmac
    harapan = hmac.new(b"SECRET", urllib.parse.quote(
        query, safe="").encode(), hashlib.sha256).hexdigest()
    # Tanda tangan dihitung dari query yang SUDAH memuat recvWindow.
    assert "recvWindow" in query
    assert ditangkap["sign"], "tanda tangan tidak boleh kosong"
