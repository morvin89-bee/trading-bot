"""Lapisan data portofolio Indodax (read-only).

Membaca saldo & riwayat transaksi dari Indodax (via `indodax_api`),
menggabungkannya dengan catatan lokal (`portfolio.db`), lalu menghitung
kepemilikan dan harga rata-rata. Tidak ada fungsi yang bisa mengirim order.
"""
import indodax_api

# Koin yang dilacak -> pair Indodax.
COIN_PAIRS = {
    "btc": "btc_idr",
    "eth": "eth_idr",
    "sol": "sol_idr",
    "bch": "bch_idr",
}


def parse_balances(info):
    """Ubah respons `account_info()` menjadi {koin: kuantitas}.

    Menerima bentuk TAPI v2 (`balances` list) maupun v1
    (`return.balance` + `balance_rp`). Saldo nol difilter; kunci huruf
    kecil, `IDR` tetap kapital.
    """
    out = {}
    if isinstance(info, dict) and isinstance(info.get("balances"), list):
        for b in info["balances"]:
            try:
                qty = float(b.get("free") or 0) + float(b.get("locked") or 0)
            except (TypeError, ValueError):
                continue
            asset = str(b.get("asset") or "").strip().lower()
            if not asset:
                continue
            key = "IDR" if asset in ("idr", "rp") else asset
            if qty > 0:
                out[key] = qty
        return out

    ret = (info or {}).get("return") or {}
    for k, v in (ret.get("balance") or {}).items():
        try:
            qty = float(v or 0)
        except (TypeError, ValueError):
            continue
        key = str(k).strip().lower()
        key = "IDR" if key in ("idr", "rp") else key
        if qty > 0:
            out[key] = qty
    try:
        rp = float(ret.get("balance_rp") or 0)
    except (TypeError, ValueError):
        rp = 0
    if rp > 0:
        out["IDR"] = rp
    return out


def fetch_balances():
    """Ambil saldo asli dari Indodax (read-only)."""
    return parse_balances(indodax_api.account_info())
