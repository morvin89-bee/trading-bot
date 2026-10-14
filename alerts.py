"""Pesan peringatan berbahasa Indonesia untuk saran aksi portofolio.

`build_alerts` murni teks (mudah diuji). `send_telegram` disiapkan untuk
notifikasi opsional dan tidak dipanggil di v1.
"""
import requests

import rules


def _rp(x):
    """Format rupiah gaya Indonesia: 400000 -> 'Rp 400.000'."""
    try:
        return "Rp " + f"{float(x):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"


def build_alerts(holdings: list, actions: "list[rules.Action]") -> list:
    """Ubah daftar `Action` menjadi pesan teks berbahasa Indonesia.

    Satu pesan per aksi (selain `HOLD`). Aksi yang belum bisa dijalankan
    (di bawah minimum order) tetap ditampilkan, dengan catatan penundaan.
    """
    out = []
    for a in actions:
        if getattr(a, "kind", "") == "HOLD":
            continue
        note = "" if a.executable else " (di bawah minimum order Rp 10.000 — tunda)"
        if a.kind == "SELL":
            out.append(f"JUAL {a.pair}: {a.reason}. Perkiraan nilai "
                       f"{_rp(a.amount_idr)}{note}.")
        elif a.kind == "BUY":
            out.append(f"BELI {a.pair}: {a.reason}. Perkiraan nilai "
                       f"{_rp(a.amount_idr)}{note}.")
        else:
            out.append(f"{a.kind} {a.pair}: {a.reason}{note}.")
    return out


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Kirim pesan via Telegram Bot API. Mengembalikan False bila gagal."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/sendMessage",
            params={"chat_id": chat_id, "text": text}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False
