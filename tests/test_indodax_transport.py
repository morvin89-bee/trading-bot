"""Tes transport Indodax: adapter antara `executor.Transport` dan TAPI v1.

Tanpa jaringan: fungsi panggil API disuntik (palsu). Tidak pernah menyentuh
kredensial asli.

Dua fakta dari dokumen resmi Indodax (Private-RestAPI.md, diunduh 2026-10-21)
yang menjadi alasan rancangan di bawah:

1. Method `trade` TIDAK punya parameter `amount`. Untuk limit beli/jual koin,
   jumlah koin dikirim lewat kunci bernama simbol koin (`btc`, `eth`, ...)
   yang diambil dari `pair` (`btc_idr` -> `btc`). `executor.payload_for`
   memakai `amount` (kosakata netral) — transport yang menerjemahkan.
2. Indodax TIDAK mengekspos izin API key (trade/withdraw) lewat API; yang
   ada `withdraw_status` (status akun). Maka `can_trade`/`can_withdraw`
   dinyatakan eksplisit lewat .env dan DEFAULTNYA AMAN (False/False) —
   tanpa deklarasi, `permissions_ok` akan berhenti, bukan kirim.
"""
import pytest

import executor
import proposal
import rules
import indodax_transport


def _proposal(kind="BUY", pair="btc_idr", qty=0.001, price=1000.0):
    action = rules.Action(
        kind=kind, pair=pair, amount_idr=qty * price, qty=qty,
        price_ref=price, reason="uji", executable=True)
    return proposal.new_proposal(
        action, rule="DCA bulanan", reserve_after=0.0,
        window_s=proposal.DCA_WINDOW_S, now=1760000000.0)


class _PanggilanPalsu:
    """Merekam (method, params) tanpa membuka jaringan; balasan bisa diatur."""
    def __init__(self, balasan=None):
        self.panggilan = []
        self._balasan = balasan or {}

    def __call__(self, method, **params):
        self.panggilan.append((method, params))
        return self._balasan


def test_post_order_pakai_kunci_koin_bukan_amount():
    # Bukti dokumen: `trade` tanpa `amount`; jumlah koin = kunci simbol koin.
    palsu = _PanggilanPalsu({"success": 1, "return": {"order_id": 1}})
    t = indodax_transport.IndodaxTransport(call=palsu)
    payload = executor.payload_for(_proposal(), "cid-1")

    t.post_order(payload)

    assert palsu.panggilan, "post_order harus memanggil API"
    method, params = palsu.panggilan[0]
    assert method == "trade"
    assert params["pair"] == "btc_idr"
    assert params["type"] == "buy"
    assert params["order_type"] == "limit"
    assert params["price"] == 1000.0
    assert params["btc"] == 0.001          # kunci koin, bukan "amount"
    assert "amount" not in params          # "amount" tidak ada di Indodax
    assert params["client_order_id"] == "cid-1"


def test_post_order_sell_juga_pakai_kunci_koin():
    palsu = _PanggilanPalsu({"success": 1, "return": {"order_id": 2}})
    t = indodax_transport.IndodaxTransport(call=palsu)
    payload = executor.payload_for(_proposal(kind="SELL"), "cid-2")

    t.post_order(payload)

    _, params = palsu.panggilan[0]
    assert params["type"] == "sell"
    assert params["btc"] == 0.001


def test_post_order_mengembalikan_balasan_bursa():
    balasan = {"success": 1, "return": {"order_id": 59632813}}
    t = indodax_transport.IndodaxTransport(call=_PanggilanPalsu(balasan))
    payload = executor.payload_for(_proposal(), "cid-3")
    assert t.post_order(payload) == balasan


def test_pair_tanpa_simbol_koin_berhenti_bukan_nebak():
    # Ragu = tidak kirim: tanpa simbol koin yang bisa dipastikan, berhenti.
    t = indodax_transport.IndodaxTransport(call=_PanggilanPalsu())
    payload = executor.payload_for(_proposal(pair="btcidr"), "cid-4")
    with pytest.raises(ValueError):
        t.post_order(payload)


def test_get_order_status_baca_status_dari_bursa():
    palsu = _PanggilanPalsu(
        {"success": 1, "return": {"order": {"status": "filled"}}})
    t = indodax_transport.IndodaxTransport(call=palsu)

    assert t.get_order_status("cid-5") == {"status": "filled"}
    method, params = palsu.panggilan[0]
    assert method == "getOrderByClientOrderId"
    assert params["client_order_id"] == "cid-5"


def test_get_order_status_tanpa_status_bukan_mengarang():
    # Balasan tanpa status yang terbaca -> dict kosong (executor -> "unknown"
    # -> berhenti), bukan status karangan.
    palsu = _PanggilanPalsu({"success": 1, "return": {"order": {}}})
    t = indodax_transport.IndodaxTransport(call=palsu)
    assert t.get_order_status("cid-6") == {}


def test_izin_default_amankan():
    # Tanpa deklarasi .env: can_trade & can_withdraw False -> permissions_ok
    # menghentikan bot, bukan mengirim.
    t = indodax_transport.IndodaxTransport(call=_PanggilanPalsu())
    assert t.can_trade is False
    assert t.can_withdraw is False


def test_factory_membangun_transport_lengkap():
    p = indodax_transport.transport_factory()
    assert hasattr(p, "post_order") and hasattr(p, "get_order_status")
    assert hasattr(p, "can_trade") and hasattr(p, "can_withdraw")
    # Default aman sampai .env menyatakan lain.
    assert p.can_trade is False and p.can_withdraw is False


def test_env_bool_hanya_1_yang_mengaktifkan(monkeypatch):
    # Keamanan: hanya "1" (persis) = izin. Salah ketik/nilai aneh = TIDAK
    # pernah berubah jadi izin; ketiadaan = aman (False).
    monkeypatch.setenv("INDODAX_CAN_TRADE", "1")
    assert indodax_transport._env_bool("INDODAX_CAN_TRADE") is True

    for nilai in ("true", "TRUE", "yes", "on", "ya", "2", "11", ""):
        monkeypatch.setenv("INDODAX_CAN_TRADE", nilai)
        assert indodax_transport._env_bool("INDODAX_CAN_TRADE") is False, nilai

    # Spasi sekeliling di-strip dulu (konsisten dengan pembaca .env), tapi
    # hanya "1" yang lolos.
    monkeypatch.setenv("INDODAX_CAN_TRADE", "  1  ")
    assert indodax_transport._env_bool("INDODAX_CAN_TRADE") is True

    monkeypatch.delenv("INDODAX_CAN_TRADE", raising=False)
    assert indodax_transport._env_bool("INDODAX_CAN_TRADE") is False
