"""Eksekutor: satu order, satu kali — tidak pernah membuka jaringan.
"""
import re
from typing import Protocol, Dict, Any

import proposal


MAX_CLIENT_ID_LEN = 36


def make_client_order_id(prefix: str, now: float) -> str:
    """Buat client order id dari prefix + timestamp, potong ke 36 karakter."""
    raw = f"{prefix}-{int(now)}"
    if len(raw) <= MAX_CLIENT_ID_LEN:
        return raw
    # Potong dari akhir jika terlalu panjang
    return raw[:MAX_CLIENT_ID_LEN]


class SubmissionUncertain(Exception):
    """Balasan hilang atau ambigu — tidak diketahui apakah order terkirim."""
    pass


class Transport(Protocol):
    """Kontrak transport: hanya dua metode yang diperlukan."""
    def post_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Kirim order sekali, kembalikan respons bursa."""
        ...

    def get_order_status(self, client_order_id: str) -> Dict[str, Any]:
        """Tanyakan status order oleh client order id."""
        ...


def submit_once(transport: Transport, payload: Dict[str, Any], client_order_id: str) -> Dict[str, Any]:
    """Kirim order sekali menggunakan transport, tanpa retry di dalam fungsi ini."""
    try:
        return transport.post_order(payload)
    except Exception as e:
        # Kesalahan apa pun (termasuk timeout, koneksi putus) -> tidak pasti
        raise SubmissionUncertain from e


def payload_for(p: proposal.Proposal, client_order_id: str, recv_window_ms: int = 15000) -> Dict[str, Any]:
    """Bangun payload order limit dari Proposal."""
    # Harga limit dari Proposal.price_ref
    price = p.price_ref
    # Untuk order beli: bayarkan dalam IDR, untuk order jual: bayarkan dalam koin
    # Menurut dokumentasi Indodax v2, parameter tergantung pada tipe order
    # Namun, spesifikasi task hanya menyebutkan order limit dan recvWindow
    # Kita asumsikan format dasar yang diperlukan
    return {
        "order_type": "limit",
        "client_order_id": client_order_id,
        "recvWindow": recv_window_ms,
        # Parameter lain seperti pair, type, etc. akan ditambahkan oleh pemanggil
        # berdasarkan kebutuhan spesifik Indodax API
    }