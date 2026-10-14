"""Aturan portofolio (logika murni, tanpa jaringan).

Berisi pembagian setoran DCA, saran take-profit, dan rebalance. Semua fungsi
di sini deterministik dan mudah diuji.
"""

from dataclasses import dataclass

# Bobot target: BTC 55%, ETH 25%, SOL 10%, cadangan IDR 10%.
DEFAULT_TARGET = {
    "btc_idr": 0.55,
    "eth_idr": 0.25,
    "sol_idr": 0.10,
    "IDR": 0.10,
}


def dca_split(amount_idr, holdings_value, target=None, min_order=10000):
    """Bagi setoran bulanan sesuai bobot target, miring ke aset termurah.

    - Bobot dasar × `amount_idr`.
    - Bila ada aset yang melebihi target, dananya dialihkan ke aset yang
      paling jauh di bawah target (proporsional).
    - Dibulatkan ke bawah ke kelipatan Rp 1.000.
    - Alokasi aset < `min_order` dipindahkan ke cadangan `IDR`.
    - Total hasil selalu sama dengan `amount_idr`.

    Mengembalikan dict dengan kunci yang sama seperti `target`.
    """
    if target is None:
        target = DEFAULT_TARGET

    base = {k: amount_idr * w for k, w in target.items()}

    total = sum(holdings_value.get(k, 0) for k in target)
    if total > 0:
        cur = {k: holdings_value.get(k, 0) / total for k in target}
        over = {k: max(0.0, cur[k] - target[k]) for k in target}
        under = {k: max(0.0, target[k] - cur[k]) for k in target}
        under_total = sum(under.values())
        if sum(over.values()) > 0 and under_total > 0:
            pool = sum(base[k] for k in target if over[k] > 0)
            for k in target:
                if over[k] > 0:
                    base[k] = 0.0
                elif under[k] > 0:
                    base[k] += pool * (under[k] / under_total)

    out = {k: (int(base[k]) // 1000) * 1000 for k in target}

    moved = 0
    for k in target:
        if k == "IDR":
            continue
        if out[k] < min_order:
            moved += out[k]
            out[k] = 0
    out["IDR"] += moved

    # Sisa pembulatan masuk ke cadangan supaya total tetap pas.
    out["IDR"] += int(amount_idr - sum(out.values()))
    return out


@dataclass
class Action:
    kind: str
    pair: str
    amount_idr: float
    qty: float | None
    price_ref: float | None
    reason: str
    executable: bool


def take_profit_actions(holdings, thresholds=((50, 25), (100, 25)),
                        fee_pct=0.002, min_order=10000):
    """Saran jual saat untung bersih melewati ambang (kumulatif).

    Pemicu dihitung **neto setelah fee taker** (`fee_pct`) memakai harga bid
    (`holding.price`). Bila beberapa ambang terlewati, persentase jual
    dijumlahkan (kumulatif +50% -> 25%, +100% -> 25%). Posisi tanpa riwayat
    beli (`basis_known` salah) dilewati.
    """
    actions = []
    for h in holdings:
        if not h.basis_known or not h.avg_cost or not h.price:
            continue
        gain_pct = (h.price * (1 - fee_pct) - h.avg_cost) / h.avg_cost * 100
        pct = 0
        level = None
        for thresh, part in thresholds:
            if gain_pct >= thresh:
                pct += part
                level = thresh
        if level is None:
            continue
        sell_qty = h.qty * pct / 100
        amount = sell_qty * h.price
        actions.append(Action(
            kind="SELL",
            pair=h.pair,
            amount_idr=amount,
            qty=sell_qty,
            price_ref=h.price,
            reason=(f"Untung bersih {gain_pct:.1f}% dari harga rata-rata "
                    f"(level +{level}%, jual kumulatif {pct}% posisi)"),
            executable=amount >= min_order,
        ))
    return actions
