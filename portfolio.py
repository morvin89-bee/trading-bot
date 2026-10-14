"""Lapisan data portofolio Indodax (read-only).

Membaca saldo & riwayat transaksi dari Indodax (via `indodax_api`),
menggabungkannya dengan catatan lokal (`portfolio.db`), lalu menghitung
kepemilikan dan harga rata-rata. Tidak ada fungsi yang bisa mengirim order.
"""
import json
import time
from dataclasses import dataclass

import indodax_api
import portfolio_store
import rules

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
            # Bila harga pasar tak tersedia, nilai aset 0 dan P&L belum
            # diketahui (bukan kerugian) -> `None`.
            unrealized = (value - qty * avg) if price else None
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


def sync_trades(conn, pairs=None):
    """Tarik riwayat transaksi dari Indodax -> `portfolio.db` (read-only).

    Menyimpan beli dan jual (bukan reward — itu dicatat manual). Kegagalan
    jaringan pada satu pair tidak menghentikan pair lain. Mengembalikan
    jumlah baris baru.
    """
    if pairs is None:
        pairs = list(COIN_PAIRS.values())
    inserted = 0
    for pair in pairs:
        try:
            trades = indodax_api.my_trades(pair)
        except Exception:
            continue
        for t in trades:
            side = (t.get("side") or "").lower()
            if side not in ("buy", "sell"):
                continue
            trade = {
                "pair": pair,
                "side": side,
                "price": t.get("price"),
                "qty": t.get("qty"),
                "quote": t.get("quote"),
                "fee": 0,
                "ts": t.get("time"),
                "source": "api",
                "external_id": f"{pair}:{t.get('id')}",
            }
            if portfolio_store.insert_trade(conn, trade):
                inserted += 1
    return inserted


def fetch_price(pair):
    """Harga **bid** Indodax untuk `pair`; `None` bila gagal (mis. 429)."""
    try:
        import scanner                      # impor malas: menghindari efek samping
        return scanner.indodax_price(pair)[0]
    except Exception:
        return None


def _breakdown_json(holdings, idr):
    items = [{"coin": "IDR", "pair": None, "qty": idr, "price": 1.0,
              "value_idr": idr}]
    for h in holdings:
        items.append({"coin": h.coin, "pair": h.pair, "qty": h.qty,
                      "price": h.price, "value_idr": h.value_idr})
    return json.dumps(items, ensure_ascii=False)


def _latest_breakdown(conn):
    snaps = portfolio_store.list_snapshots(conn)
    if not snaps:
        return []
    try:
        return json.loads(snaps[-1].get("breakdown_json") or "[]")
    except (ValueError, TypeError):
        return []


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _snapshot_done_today(conn, now):
    snaps = portfolio_store.list_snapshots(conn)
    if not snaps:
        return False
    return _day(snaps[-1].get("ts") or 0) == _day(now)


def _write_daily_snapshot(conn, total, idr, holdings):
    """Tulis satu snapshot, maksimal sekali per hari (waktu lokal)."""
    now = int(time.time())
    if _snapshot_done_today(conn, now):
        return False
    contributions = portfolio_store.total_contributions(conn)
    portfolio_store.insert_snapshot(conn, {
        "ts": now,
        "total_idr": total,
        "contributions_idr": contributions,
        "market_idr": total - contributions,
        "breakdown_json": _breakdown_json(holdings, idr),
    })
    return True


def refresh(conn, online=True):
    """Segarkan portofolio, hitung saran aksi, dan (bila online) snapshot.

    Bila `online` benar, tarik riwayat transaksi dan saldo terbaru dari
    Indodax (read-only) serta harga pasar, lalu tulis snapshot — **maksimal
    sekali per hari** (membuka dasbor berulang tidak menambah baris). Bila
    salah, pakai snapshot terakhir sebagai cache tanpa jaringan dan tanpa
    menulis apa pun.

    Mengembalikan `{"holdings": [...], "total_idr": float, "actions": [...]}`.
    """
    if online:
        sync_trades(conn)
        balances = fetch_balances()
        prices = {pair: fetch_price(pair) for pair in COIN_PAIRS.values()}
    else:
        items = _latest_breakdown(conn)
        balances = {it["coin"]: it["qty"] for it in items}
        prices = {it["pair"]: it["price"] for it in items if it.get("pair")}

    idr = float(balances.get("IDR", 0) or 0)
    coins = {k: v for k, v in balances.items() if k != "IDR"}

    trades = portfolio_store.list_trades(conn)
    holdings = compute_holdings(trades, coins, prices)
    market = sum(h.value_idr for h in holdings)
    total = idr + market

    # Ambil saran take-profit lalu yang lolos band. Keduanya bisa menunjuk
    # pair yang sama, jadi buang duplikat (kalau tidak, satu posisi tampak
    # disarankan "jual 50%" padahal hanya 25%).
    seen = set()
    actions = []
    for a in (rules.take_profit_actions(holdings)
              + rules.rebalance_actions(holdings, idr_reserve=idr)):
        key = (a.kind, a.pair)
        if key in seen:
            continue
        seen.add(key)
        actions.append(a)

    if online:
        _write_daily_snapshot(conn, total, idr, holdings)
    return {"holdings": holdings, "total_idr": total, "actions": actions}
