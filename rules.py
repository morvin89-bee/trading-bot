"""Aturan portofolio (logika murni, tanpa jaringan).

Berisi pembagian setoran DCA, saran take-profit, dan rebalance. Semua fungsi
di sini deterministik dan mudah diuji.
"""

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
