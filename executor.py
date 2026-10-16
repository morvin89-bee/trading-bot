"""Eksekutor: satu order, satu kali — tidak pernah membuka jaringan.
"""
import re
import time
from typing import Protocol, Dict, Any

import proposal


MAX_CLIENT_ID_LEN = 36
STATUS_BACKOFF_S = (2, 5, 10)


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
    """Bangun payload order limit dari Proposal — SATU-SATUNYA tempat nama
    parameter POST dibangun (putusan F7; verifikasi hari-1 ada di sini).

    Kosakata diambil HANYA dari bukti yang ada di repo: `type`, `price`,
    dan `amount` adalah kunci yang dibaca `_normalize_v1` dan
    `_normalize_v2` (indodax_api.py:148-201) dari catatan trade; `pair`
    adalah kunci pasangan di seluruh indodax_api.py (mis. `my_trades(pair)`).

    GERBANG VERIFIKASI (sama dengan `client_order_id`, putusan F7):
    `indodax_api.py` TIDAK memuat fungsi penempatan order — sengaja,
    supaya tidak ada kunci dagang di repo — jadi kontrak order tidak ada
    di repo. EJAAN PERSIS parameter penempatan order di bawah ini karena
    itu BELUM TERVERIFIKASI dan wajib diverifikasi sebelum eksekusi
    diaktifkan. Jangan menyebarkan nama parameter ke modul lain.
    """
    return {
        "order_type": "limit",
        "client_order_id": client_order_id,
        "recvWindow": recv_window_ms,
        # Empat field instrumen (putusan R-A): tanpa ini order terkirim
        # tanpa instrumen. Nilai memakai kosakata catatan trade — `type`
        # = sisi "buy"/"sell" (indodax_api.py:157 dan 184-190), `amount`
        # = jumlah KOIN seperti `amount` catatan trade v1
        # (indodax_api.py:149), `price` = harga limit dari `price_ref`.
        "pair": p.pair,
        "type": p.kind.lower(),
        "price": p.price_ref,
        "amount": p.qty,
    }


def resolve_status(transport: Transport, client_order_id: str, *, sleep=time.sleep) -> str:
    """Query order status with up to three attempts, using backoff [2,5,10] seconds before each attempt."""
    for i in range(3):
        sleep(STATUS_BACKOFF_S[i])
        response = transport.get_order_status(client_order_id)
        status_val = response.get("status")
        if status_val is None or not isinstance(status_val, str):
            # No status key or non-string status -> treat as unknown for this attempt, continue to next
            continue
        status_str = status_val.upper()
        if status_str == "FILLED":
            return "filled"
        elif status_str == "CANCELLED":
            return "cancelled"
        elif status_str == "REJECTED":
            return "rejected"
        # If status is not one of the known ones, treat as unknown and continue
    return "unknown"