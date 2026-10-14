"""Lapisan data portofolio Indodax (read-only).

Membaca saldo & riwayat transaksi dari Indodax (via `indodax_api`),
menggabungkannya dengan catatan lokal (`portfolio.db`), lalu menghitung
kepemilikan dan harga rata-rata. Tidak ada fungsi yang bisa mengirim order.
"""
from dataclasses import dataclass

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


@dataclass
class Holding:
    pair: str
    coin: str
    qty: float
    avg_cost: float | None
    price: float | None
    value_idr: float
    cost_basis_idr: float | None
    unrealized_idr: float | None
    basis_known: bool


def _accumulate(trades):
    """Proses daftar trade per pair.

    Mengembalikan {pair: {"qty_bought", "cost", "realized", "buys_exist"}}.
    Harga rata-rata = `cost / qty_bought`; reward staking menambah kuantitas
    tanpa menambah biaya, sehingga tidak menaikkan harga rata-rata.
    """
    state = {}
    for t in sorted(trades, key=lambda x: (x.get("ts") or 0, x.get("id") or 0)):
        pair = t.get("pair")
        side = (t.get("side") or "").lower()
        if not pair or side not in ("buy", "sell", "reward"):
            continue
        s = state.setdefault(
            pair, {"qty_bought": 0.0, "cost": 0.0, "realized": 0.0,
                   "buys_exist": False})
        qty = float(t.get("qty") or 0)
        quote = float(t.get("quote") or 0)
        fee = float(t.get("fee") or 0)
        if side == "buy":
            s["qty_bought"] += qty
            s["cost"] += quote + fee
            s["buys_exist"] = True
        elif side == "sell":
            avg = (s["cost"] / s["qty_bought"]) if s["qty_bought"] > 0 else 0.0
            s["realized"] += quote - avg * qty
            s["cost"] -= avg * qty
            s["qty_bought"] -= qty
            if s["qty_bought"] <= 0:      # terjual habis (mungkin lebih, krn reward)
                s["qty_bought"] = 0.0
                s["cost"] = 0.0
        # reward: menambah kuantitas tanpa biaya
    return state


def realized_pnl(trades):
    """Keuntungan/kerugian yang sudah terealisasi (per pair), dalam IDR."""
    return {pair: round(s["realized"], 2)
            for pair, s in _accumulate(trades).items()}


def compute_holdings(trades, balances, prices):
    """Bentuk daftar `Holding` dari saldo, harga, dan riwayat transaksi.

    Kuantitas diambil dari `balances` (otoritatif). Harga rata-rata hanya
    tersedia bila ada riwayat pembelian untuk koin itu (`basis_known`).
    """
    state = _accumulate(trades)
    out = []
    for coin, qty in balances.items():
        if coin == "IDR":
            continue
        pair = COIN_PAIRS.get(coin)
        if not pair:
            continue
        s = state.get(pair)
        price = prices.get(pair)
        value = qty * price if price else 0.0
        if s and s["buys_exist"]:
            avg = (s["cost"] / s["qty_bought"]) if s["qty_bought"] > 0 else 0.0
            cost_basis = s["cost"]
            unrealized = value - qty * avg
            basis_known = True
        else:
            avg = None
            cost_basis = None
            unrealized = None
            basis_known = False
        out.append(Holding(pair, coin, qty, avg, price, value, cost_basis,
                           unrealized, basis_known))
    out.sort(key=lambda h: h.value_idr, reverse=True)
    return out
