"""Telegram gateway for sending confirmation messages with inline keyboard
and simple line notifications.
"""

import json
from typing import Optional

import requests

import alerts
import proposal

BUTTON_SETUJU = "setuju"
BUTTON_TUNDA = "tunda"
BUTTON_BATAL = "batal"


def get_updates(token: str, *, offset: int = 0, fetch=None) -> tuple[list[dict], int]:
    """Wrapper around Telegram getUpdates method with timeout=0.
    
    Args:
        token: Bot token
        offset: Identifier of the first update to be returned
        fetch: Injected fetch function for testing (defaults to real API)
    
    Returns:
        Tuple of (updates list, next offset)
    """
    if fetch is None:
        # Default to real Telegram API - for production use
        # Since we shouldn't make real network calls in tests per constraints,
        # this should be overridden with a mock in tests
        raise NotImplementedError("Fetch function must be provided for production use")

    # Call the fetch function with timeout=0 as specified
    response = fetch(token, offset=offset, timeout=0)
    
    # Extract updates from response
    updates = response.get("result", [])
    
    # Calculate next offset
    next_offset = offset
    if updates:
        next_offset = max(update["update_id"] for update in updates) + 1
    
    return updates, next_offset


def live_fetch(token: str, *, offset: int = 0, timeout: int = 0,
               get=requests.get) -> dict:
    """Fetch produksi `getUpdates` — satu-satunya pembaca jaringan Telegram.

    Dipanggil HANYA dari blok `__main__` (`main_loop`) sebagai `fetch`;
    tes selalu menyuntikkan `get`/`fetch` palsu, jadi tidak ada tes yang
    boleh menyentuh jaringan. Kegagalan jaringan dibiarkan MELEMPAR —
    pemanggil (`main_loop`) yang menahan dan mencatat, tanpa mengarang
    balasan (prinsip ragu = tidak kirim).

    `timeout` Telegram 0 = balas segera tanpa long-poll; timeout HTTP
    minimal 10 detik supaya permintaan tidak diputus lebih dulu.
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    hasil = get(url, params={"offset": offset, "timeout": timeout},
                timeout=max(timeout, 10))
    # `requests.get` TIDAK melempar pada status 4xx/5xx, dan Telegram balas
    # {"ok": false} untuk 401 (token mati), 404, 409 (getUpdates ganda),
    # 429, dan 5xx. Tanpa dua pemeriksaan ini balasan gagal dianggap
    # "tanpa update" oleh `get_updates` — polling terlihat sehat tanpa satu
    # baris log (kontrak §3.1: gagal = melempar, pemanggil yang mencatat).
    periksa = getattr(hasil, "raise_for_status", None)
    if periksa is not None:  # fake bertest sengaja tidak punya metode ini
        periksa()
    data = hasil.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram menolak: {data.get('error_code')} "
            f"{data.get('description')}")
    return data


def parse_update(update: dict) -> tuple[str, str] | None:
    """Extract callback data from inline keyboard or /ulang command.
    
    Args:
        update: Update object from Telegram getUpdates
    
    Returns:
        Tuple of (action, id) for callback queries, ("ulang", "") for /ulang command,
        or None for everything else
    """
    # Handle callback queries from inline keyboard
    if "callback_query" in update and "data" in update["callback_query"]:
        data = update["callback_query"]["data"]
        if data.startswith(f"{BUTTON_SETUJU}:"):
            return ("setuju", data.split(":", 1)[1])
        elif data.startswith(f"{BUTTON_TUNDA}:"):
            return ("tunda", data.split(":", 1)[1])
        elif data.startswith(f"{BUTTON_BATAL}:"):
            return ("batal", data.split(":", 1)[1])
    
    # Handle /ulang command
    if "message" in update and "text" in update["message"]:
        text = update["message"]["text"]
        if text == "/ulang":
            return ("ulang", "")
    
    # Everything else returns None
    return None


class Telegram:
    def __init__(self, token: str, chat_id: str, *, send=None):
        self.token = token
        self.chat_id = chat_id
        self.send = send

    def line(self, text: str) -> bool:
        """Send a simple one-line message without keyboard.
        Returns True on success, False on failure.
        """
        try:
            return self._send(text, None)
        except Exception:
            return False

    def confirmation(self, p: proposal.Proposal) -> Optional[int]:
        """Send confirmation message with inline keyboard.
        Returns the message_id from the send function (or True/False from API).
        Returns None on failure.
        """
        text = proposal.confirmation_text(p)
        keyboard = [
            [
                {"text": "Setuju", "callback_data": f"{BUTTON_SETUJU}:{p.id}"},
                {"text": "Tunda", "callback_data": f"{BUTTON_TUNDA}:{p.id}"},
                {"text": "Batal bulan ini", "callback_data": f"{BUTTON_BATAL}:{p.id}"},
            ]
        ]
        reply_markup = {"inline_keyboard": keyboard}
        try:
            return self._send(text, reply_markup)
        except Exception:
            return None

    def _send(self, text: str, reply_markup: Optional[dict]):
        if self.send is not None:
            return self.send(self.token, self.chat_id, text, reply_markup=reply_markup)
        else:
            # Use the real Telegram API via alerts.send_telegram
            return alerts.send_telegram(self.token, self.chat_id, text, reply_markup=reply_markup)