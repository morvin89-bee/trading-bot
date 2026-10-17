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

# Satu-satunya rumah angka setoran bulanan; `dashboard` menyalin dari sini.
MONTHLY_IDR = 500000
# Nama aturan DCA untuk provenance (F32) dan gerbang F46.
RULE_DCA = "dca_bulanan"


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

    # Sisa pembulatan masuk ke cadangan supaya total tetap pas (termasuk bila
    # `amount_idr` bukan bilangan bulat).
    out["IDR"] += amount_idr - sum(out.values())
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


def rebalance_actions(holdings, target=None, band_pct=10.0,
                      fee_pct=0.002, min_order=10000, idr_reserve=0.0):
    """Saran jual untuk aset yang menyimpang jauh di atas target.

    Hanya menghasilkan `SELL`, dan hanya bila **dua-duanya** benar:
    (1) bobot aset melebihi target lebih dari `band_pct` poin persen, dan
    (2) aset memicu take-profit. **Tidak pernah** menghasilkan `BUY`, jadi
    cadangan IDR tidak pernah dibelanjakan oleh rebalance (aliran DCA yang
    menambah posisi; band hanya memangkas yang kelewat berat).

    `idr_reserve` adalah saldo IDR (cadangan). Karena target dinyatakan atas
    **seluruh** portofolio, cadangan ikut dihitung sebagai penyebut.
    """
    if target is None:
        target = DEFAULT_TARGET
    total = sum(h.value_idr for h in holdings) + idr_reserve
    if total <= 0:
        return []

    actions = []
    for h in holdings:
        target_share = target.get(h.pair, 0.0)
        share = h.value_idr / total
        if (share - target_share) * 100 > band_pct:
            actions.extend(
                take_profit_actions([h], fee_pct=fee_pct, min_order=min_order))
    return actions


def max_position_ok(amount_idr, total_idr, max_pct=0.25):
    """True bila menambah `amount_idr` masih di bawah batas porsi `max_pct`."""
    return amount_idr <= max_pct * total_idr


def reserve_buy_actions(holdings, idr_reserve, target=None, monthly=MONTHLY_IDR,
                        drop_pct=15.0, max_fraction=0.5, min_order=10000):
    """Saran **BELI** memakai sebagian cadangan saat aset jatuh (spec 6.4).

    Untuk tiap aset yang harga bid-nya turun minimal `drop_pct` persen dari
    harga rata-rata, sarankan membeli satu jatah bulanan target aset itu
    (`target[pair] * monthly`), diurutkan dari yang paling jatuh. Total
    belanja dibatasi `max_fraction * idr_reserve`, jadi sebagian besar
    cadangan tetap aman. Posisi tanpa harga rata-rata dilewati.
    """
    if target is None:
        target = DEFAULT_TARGET
    budget = max(0.0, idr_reserve) * max_fraction

    cands = []
    for h in holdings:
        if not h.basis_known or not h.avg_cost or not h.price:
            continue
        drop = (h.avg_cost - h.price) / h.avg_cost * 100
        if drop >= drop_pct:
            cands.append((drop, h))
    cands.sort(key=lambda x: x[0], reverse=True)

    actions = []
    for drop, h in cands:
        if budget < min_order:
            break
        tranche = target.get(h.pair, 0.0) * monthly
        amount = min(tranche, budget)
        if amount < min_order:
            continue
        actions.append(Action(
            kind="BUY",
            pair=h.pair,
            amount_idr=amount,
            qty=amount / h.price,
            price_ref=h.price,
            reason=(f"Turun {drop:.1f}% dari harga rata-rata (ambang "
                    f"-{drop_pct:.0f}%) -> beli pakai sebagian cadangan"),
            executable=True,
        ))
        budget -= amount
    return actions


def dca_actions(holdings, idr_free, prices, *, max_order, monthly=MONTHLY_IDR,
                target=None, min_order=10000):
    """Saran **BELI** DCA bulanan: bagi setoran sesuai bobot target (F45).

    `monthly` dibagi oleh `dca_split` memakai nilai portofolio (`holdings`
    + `idr_free`) lalu tiap alokasi dipotong ke `max_order` — kelebihannya
    TIDAK dibagikan ulang, tetap sebagai cadangan IDR (bisa dipakai
    reserve buy). `max_order` wajib diisi pemanggil (dari
    `safeguards.MAX_ORDER_IDR`); `rules` tidak boleh mengimpor
    `safeguards` (circular). Aset tanpa harga (`prices` kosong/nol)
    dilewati — `price_ref` tidak bisa diisi dengan jujur.
    """
    if target is None:
        target = DEFAULT_TARGET
    nilai = {h.pair: h.value_idr for h in holdings}
    nilai["IDR"] = idr_free
    alokasi = dca_split(monthly, nilai, target, min_order=min_order)

    actions = []
    for pair in target:
        if pair == "IDR":
            continue
        nominal = alokasi.get(pair, 0)
        if nominal <= 0:
            continue
        harga = prices.get(pair)
        if not harga:
            continue  # tanpa harga yang jujur, price_ref tak bisa diisi
        jumlah = min(nominal, max_order)
        reason = "DCA bulanan: beli sesuai bobot target"
        if jumlah < nominal:
            reason += " (dipotong pada batas per order, sisa tetap cadangan)"
        actions.append(Action(
            kind="BUY",
            pair=pair,
            amount_idr=jumlah,
            qty=jumlah / harga,
            price_ref=harga,
            reason=reason,
            executable=True,
        ))
    return actions
