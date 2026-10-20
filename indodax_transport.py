"""Transport Indodax: adapter antara kontrak `executor.Transport` dan TAPI v1.

Menyentuh jaringan HANYA lewat `indodax_api._v1_call` (yang sudah bertanda
tangan HMAC). Modul ini adalah SATU-SATU-NYA tempat parameter order diterjemahkan
ke ejaan wire Indodax.

Dua fakta dokumen resmi (Private-RestAPI.md, btcid/indodax-official-api-docs,
diambil 2026-10-21) yang menjadi alasan desain:

1. Method `trade` TIDAK memuat parameter `amount`. Untuk limit beli/jual koin,
   jumlah koin dikirim lewat kunci bernama SIMBOL KOIN yang diambil dari `pair`
   (`btc_idr` -> `btc`), nilai `idr` hanya untuk beli ber-nominal rupiah.
   `executor.payload_for` memakai `amount` (kosakata netral, sengaja) —
   penerjemahan ke ejaan Indodax terjadi DI SINI, bukan di payload_for.

2. Indodax TIDAK mengekspos izin API key (trade/withdraw) lewat API; respons
   `getInfo` hanya memuat `withdraw_status` (status AKUN, bukan izin KEY).
   `safeguards.permissions_ok` mengasumsikan bursa melaporkan `canTrade` /
   `canWithdraw` — asumsi itu TIDAK berlaku untuk Indodax. Maka izin
   dinyatakan EKSPLEISIT lewat .env dan DEFAULTNYA AMAN (False/False):
   tanpa deklarasi, `permissions_ok` MENGHENTIKAN bot, bukan mengirim.
"""
import os

import indodax_api

ENV_FILE = indodax_api.ENV_FILE


def _env_bool(nama):
    """Baca nilai bool dari .env / environment; apa pun selain "1" = False.

    Default aman (False) supaya ketiadaan konfigurasi menghasilkan berhenti,
    bukan kirim. Hanya "1" (persis) dianggap True — huruf besar/kecil lain
    sengaja TIDAK diterima supaya salah ketik tidak pernah berubah jadi izin.
    """
    env = indodax_api._load_env()
    mentah = os.environ.get(nama) or env.get(nama, "")
    return str(mentah).strip() == "1"


def _pasangan_ke_koin(pair):
    """Simbol koin dari pair Indodax (`btc_idr` -> `btc`).

    Ragu = tidak kirim: tanpa pemisah `_` yang menghasilkan simbol non-kosong,
    berhenti dengan ValueError, bukan menebak.
    """
    koin = str(pair).split("_", 1)[0].strip()
    if not koin or koin == str(pair):
        raise ValueError(
            f"Pair {pair!r} tidak menghasilkan simbol koin yang pasti — "
            "order tidak dikirim.")
    return koin


class IndodaxTransport:
    """Implementasi `executor.Transport` di atas TAPI v1 Indodax.

    `call` disuntik (default `indodax_api._v1_call`) supaya tes tidak pernah
    membuka jaringan. `can_trade`/`can_withdraw` disuntik juga — nilai bawaan
    aman (False) dan, pada pemakaian produksi, dibaca dari .env oleh
    `transport_factory`.
    """

    def __init__(self, call=None, can_trade=False, can_withdraw=False):
        self._call = indodax_api._v1_call if call is None else call
        self.can_trade = can_trade
        self.can_withdraw = can_withdraw

    def post_order(self, payload):
        """Kirim satu order limit; kembalikan balasan mentah bursa."""
        koin = _pasangan_ke_koin(payload["pair"])
        params = {
            "pair": payload["pair"],
            "type": payload["type"],
            "order_type": payload.get("order_type", "limit"),
            "price": payload["price"],
            koin: payload["amount"],         # jumlah koin di bawah kunci simbol
            "client_order_id": payload["client_order_id"],
            "recvWindow": payload.get("recvWindow", 15000),
        }
        return self._call("trade", **params)

    def get_order_status(self, client_order_id):
        """Tanya status order by client id; {} bila tidak terbaca (-> "unknown")."""
        balasan = self._call("getOrderByClientOrderId",
                             client_order_id=client_order_id)
        pesanan = (balasan.get("return") or {}).get("order") or {}
        status = pesanan.get("status")
        if not isinstance(status, str) or not status:
            return {}                       # jangan mengarang status
        return {"status": status}


def transport_factory():
    """Bangun `IndodaxTransport` untuk produksi, dengan izin dari .env.

    Dipanggil `main_loop` HANYA lewat `main(transport_factory=...)`, yang
    berarti go-live adalah keputusan sadar pemilik — bukan kecelakaan.
    Izin dibaca dari .env (INDODAX_CAN_TRADE / INDODAX_CAN_WITHDRAW); tanpa
    deklarasi keduanya False sehingga `permissions_ok` menghentikan bot.
    """
    return IndodaxTransport(
        can_trade=_env_bool("INDODAX_CAN_TRADE"),
        can_withdraw=_env_bool("INDODAX_CAN_WITHDRAW"),
    )
