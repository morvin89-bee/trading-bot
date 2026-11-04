"""Stable facade for the production trading-bot modules.

Implementasi tetap berada di modul asli di root proyek. Modul ini hanya
mengekspor API yang stabil agar pemanggil tidak perlu tahu lokasi internal.
"""

from .dca_window import mark_pending
from .engine import Context, Proposal, on_answer
from .strategy import MeanReversionStrategy
from .utils import _fresh_db, _TelegramPerekam, _ts

__all__ = [
    "_ts",
    "_fresh_db",
    "_TelegramPerekam",
    "mark_pending",
    "Context",
    "Proposal",
    "on_answer",
    "MeanReversionStrategy",
    "main",
]


def __getattr__(name):
    """Impor `main` secara lazy agar `python -m core.main_loop` tidak memicu runpy."""
    if name == "main":
        from .main_loop import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
