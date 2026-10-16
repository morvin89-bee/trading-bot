"""Telegram gateway for sending confirmation messages with inline keyboard
and simple line notifications.
"""

import json
from typing import Optional

import alerts
import proposal


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
                {"text": "Setuju", "callback_data": f"setuju:{p.id}"},
                {"text": "Tunda", "callback_data": f"tunda:{p.id}"},
                {"text": "Batal bulan ini", "callback_data": f"batal:{p.id}"},
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