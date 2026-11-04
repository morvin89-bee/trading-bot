"""Harga publik Indodax untuk bot semi-otomatis (read-only).

Menggantikan ketergantungan `portfolio.fetch_price` pada `scanner.py`.
Tidak pernah raise: kegagalan berarti `None`, dan pemanggil memakai cache.
"""
import time

import requests

from indodax_api import UA

TICKER_URL = "https://indodax.com/api/ticker/{pair}"
TIMEOUT = 20
JEDA_RETRY_MAKS = 5.0


def _jeda_retry_after(resp):
    try:
        jeda = float(resp.headers.get("Retry-After", "2"))
    except (TypeError, ValueError):
        jeda = 2.0
    time.sleep(min(max(jeda, 0.0), JEDA_RETRY_MAKS))


def indodax_price(pair_id):
    """(bid, ask) Indodax, atau None bila gagal/429/data rusak."""
    try:
        resp = requests.get(
            TICKER_URL.format(pair=pair_id),
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
        if resp.status_code == 429:
            _jeda_retry_after(resp)
            return None
        resp.raise_for_status()
        ticker = resp.json()["ticker"]
        return float(ticker["buy"]), float(ticker["sell"])
    except Exception:
        return None
