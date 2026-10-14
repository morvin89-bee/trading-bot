"""
Analisis & prediksi statistik dari data pemantauan transaksi.

Yang dipelajari dari data:
- Rasio beli/jual (tekanan pasar)
- Volume per window (5 menit)
- Ukuran trade (deteksi whale)
- Momentum harga dalam window

Prediksi (HANYA statistik, bukan jaminan):
- Probabilitas empiris: "jika tekanan beli & volume tinggi,
  berapa % harga naik 5 menit berikutnya" dari data historis kita
- Regresi logistik sederhana (pure Python) sebagai pembanding
- Validasi walk-forward: latih masa lalu, uji masa depan
  (evaluasi jujur vs akurasi in-sample yang optimistis)

PERINGATAN: pasar kripto cenderung random walk. Akurasi ~50%
adalah hal normal dan menandakan model tidak punya nilai
prediksi nyata. Proyek edukasi, bukan sinyal trading.
"""

import math
import sqlite3
from collections import defaultdict
from datetime import datetime

DB_FILE = "trades.db"
WINDOW_SEC = 300          # window analisis: 5 menit
BUY_RATIO_THRESHOLD = 0.55
MIN_DATA = 500            # minimal transaksi sebelum analisis


def load_buckets(conn):
    """Kelompokkan transaksi jadi bucket per pair per WINDOW_SEC."""
    rows = conn.execute(
        "SELECT pair, date, price, amount, type FROM trades ORDER BY date"
    ).fetchall()
    buckets = defaultdict(list)
    for pair, date, price, amount, ttype in rows:
        buckets[(pair, date // WINDOW_SEC)].append(
            {"price": price, "amount": amount, "type": ttype}
        )
    return buckets


def load_imbalance(conn):
    """Rata-rata order book imbalance per pair per window 5 menit."""
    rows = conn.execute(
        "SELECT pair, date, imbalance FROM depth_snapshots"
    ).fetchall()
    acc = defaultdict(list)
    for pair, date, imb in rows:
        acc[(pair, date // WINDOW_SEC)].append(imb)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def summarize(trades):
    """Hitung fitur dari daftar transaksi dalam 1 window."""
    n_buy = sum(1 for t in trades if t["type"] == "buy")
    n_sell = sum(1 for t in trades if t["type"] == "sell")
    n = n_buy + n_sell
    if n == 0:
        return None
    vol_buy = sum(t["price"] * t["amount"] for t in trades if t["type"] == "buy")
    vol_sell = sum(t["price"] * t["amount"] for t in trades if t["type"] == "sell")
    total_vol = vol_buy + vol_sell
    prices = [t["price"] for t in trades]
    values = [t["price"] * t["amount"] for t in trades]
    return {
        "n": n,
        "buy_ratio": n_buy / n,
        "vol_ratio": (vol_buy / total_vol) if total_vol > 0 else 0.5,
        "total_vol": total_vol,
        "max_trade": max(values),
        "change_pct": (prices[-1] / prices[0] - 1) * 100,
        "first_price": prices[0],
        "last_price": prices[-1],
    }


def build_dataset(conn):
    """Bangun dataset berlabel: fitur window + apakah harga naik
    di window berikutnya."""
    buckets = load_buckets(conn)
    first_prices = {k: v[0]["price"] for k, v in buckets.items()}
    imbalances = load_imbalance(conn)
    data = []
    for (pair, bid), trades in buckets.items():
        s = summarize(trades)
        if not s:
            continue
        next_price = first_prices.get((pair, bid + 1))
        if next_price is None:
            continue
        s["pair"] = pair
        s["bucket_id"] = bid
        s["up"] = 1 if next_price > s["last_price"] else 0
        s["imbalance"] = imbalances.get((pair, bid), 0.0)
        data.append(s)
    return data


def empirical_table(data):
    """Probabilitas empiris P(naik) per kombinasi sinyal."""
    vols = sorted(d["total_vol"] for d in data)
    med_vol = vols[len(vols) // 2]
    groups = defaultdict(list)
    for d in data:
        key = (
            d["buy_ratio"] >= BUY_RATIO_THRESHOLD,
            d["total_vol"] >= med_vol,
        )
        groups[key].append(d["up"])
    print(f"\nVolume median per window: Rp {med_vol:,.0f}")
    print("\nTekanan beli | Volume tinggi | P(naik 5m) | Jumlah window")
    print("-" * 60)
    for (bp, vs), ups in sorted(groups.items()):
        p = sum(ups) / len(ups) * 100
        print(
            f"{'ya' if bp else 'tidak':12} | {'ya' if vs else 'tidak':12} | "
            f"{p:9.1f}% | {len(ups)}"
        )


def imbalance_table(data):
    """P(naik) berdasarkan order book imbalance (terciles)."""
    imbs = [d["imbalance"] for d in data if d["imbalance"] != 0]
    if not imbs:
        print("\nBelum ada data order book. Jalankan monitor.py lebih lama.")
        return
    imbs_sorted = sorted(imbs)
    t1 = imbs_sorted[len(imbs_sorted) // 3]
    t2 = imbs_sorted[2 * len(imbs_sorted) // 3]
    groups = {"negatif": [], "netral": [], "positif": []}
    for d in data:
        i = d["imbalance"]
        if i == 0:
            continue
        if i < t1:
            groups["negatif"].append((i, d["up"]))
        elif i < t2:
            groups["netral"].append((i, d["up"]))
        else:
            groups["positif"].append((i, d["up"]))
    print("\nOrder Book Imbalance (terciles):")
    print("Kelompok  | Rerata imb | P(naik 5m) | Jumlah")
    print("-" * 62)
    for key in ("negatif", "netral", "positif"):
        rows = groups[key]
        if not rows:
            continue
        avg_i = sum(i for i, _ in rows) / len(rows)
        p = sum(u for _, u in rows) / len(rows) * 100
        print(f"{key:9} | {avg_i:+10.3f} | {p:9.1f}% | {len(rows)}")


def train_logistic(X, y, lr=0.5, epochs=1000):
    """Regresi logistik multi-fitur, gradient descent (pure Python)."""
    n_feat = len(X[0])
    w = [0.0] * n_feat
    b = 0.0
    for _ in range(epochs):
        for xi, yi in zip(X, y):
            z = b + sum(wi * x for wi, x in zip(w, xi))
            z = max(-500, min(500, z))
            p = 1 / (1 + math.exp(-z))
            err = p - yi
            b -= lr * err
            for j in range(n_feat):
                w[j] -= lr * err * xi[j]
    return w, b


def logistic_eval(data):
    """Latih & evaluasi regresi logistik (fitur: tekanan beli,
    momentum, imbalance)."""
    X = [
        [(d["buy_ratio"] - 0.5) * 10, d["change_pct"], d["imbalance"]]
        for d in data
    ]
    y = [d["up"] for d in data]
    w, b = train_logistic(X, y)
    correct = 0
    for xi, yi in zip(X, y):
        z = b + sum(wi * x for wi, x in zip(w, xi))
        p = 1 / (1 + math.exp(-max(-500, min(500, z))))
        if (p >= 0.5) == (yi == 1):
            correct += 1
    acc = correct / len(data) * 100
    print(f"\nRegresi logistik (fitur: tekanan beli, momentum, imbalance):")
    print(f"  Akurasi pada data historis: {acc:.1f}%")
    print(f"  Catatan: ~50% = model tidak bisa memprediksi (random walk)")
    return w, b


def walk_forward_eval(data, train_min=200, test_size=100):
    """Validasi walk-forward: latih dengan data MASA LALU,
    uji pada data MASA DEPAN, lalu geser maju berulang.

    Ini evaluasi yang benar secara ilmiah (berbeda dengan
    akurasi in-sample yang selalu optimistis).
    """
    data = sorted(data, key=lambda d: d["bucket_id"])
    folds = []
    start = 0
    while start + train_min + test_size <= len(data):
        train = data[start:start + train_min]
        test = data[start + train_min:start + train_min + test_size]

        X_tr = [
            [(d["buy_ratio"] - 0.5) * 10, d["change_pct"], d["imbalance"]]
            for d in train
        ]
        y_tr = [d["up"] for d in train]
        w, b = train_logistic(X_tr, y_tr)

        # baseline: selalu prediksi kelas mayoritas di data latih
        majority = 1 if sum(y_tr) >= len(y_tr) / 2 else 0

        correct = 0
        base_correct = 0
        for d in test:
            xi = [(d["buy_ratio"] - 0.5) * 10, d["change_pct"], d["imbalance"]]
            z = b + sum(wi * x for wi, x in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-500, min(500, z))))
            pred = 1 if p >= 0.5 else 0
            if pred == d["up"]:
                correct += 1
            if majority == d["up"]:
                base_correct += 1

        folds.append(
            {
                "t0": test[0]["bucket_id"] * WINDOW_SEC,
                "acc": correct / len(test) * 100,
                "base": base_correct / len(test) * 100,
                "n": len(test),
            }
        )
        start += test_size

    if not folds:
        print(f"\nWalk-forward: data belum cukup "
              f"(butuh >= {train_min + test_size} window, "
              f"punya {len(data)}).")
        return

    print("\nValidasi walk-forward (latih masa lalu -> uji masa depan):")
    print("Waktu uji          | Akurasi model | Baseline | N")
    print("-" * 62)
    for f in folds:
        ts = datetime.fromtimestamp(f["t0"]).strftime("%d %b %H:%M")
        print(f"{ts:18} | {f['acc']:11.1f}% | {f['base']:7.1f}% | {f['n']}")

    avg = sum(f["acc"] for f in folds) / len(folds)
    avg_base = sum(f["base"] for f in folds) / len(folds)
    print("-" * 62)
    print(f"{'RATA-RATA':18} | {avg:11.1f}% | {avg_base:7.1f}% |")
    print(f"Folds: {len(folds)} | Total window diuji: {sum(f['n'] for f in folds)}")
    if avg > avg_base + 3:
        print("Model sedikit lebih baik dari baseline (>3%) — "
              "tetap belum tentu menguntungkan setelah fee.")
    else:
        print("Model TIDAK mengalahkan baseline -> tidak ada "
              "keunggulan prediksi yang bisa dipakai.")


def live_signal(conn):
    """Kondisi window terakhir (masih berjalan) per pair."""
    buckets = load_buckets(conn)
    now_bucket = max(bid for (_, bid) in buckets)
    # snapshot imbalance terbaru per pair
    latest_imb = {}
    for pair, imb in conn.execute(
        "SELECT pair, imbalance FROM depth_snapshots ORDER BY id DESC"
    ).fetchall():
        latest_imb.setdefault(pair, imb)
    print(f"\nSinyal window terkini (window #{now_bucket}, belum lengkap):")
    print("Pair     | Rasio beli | Imbalance | Volume (Rp)  | Trade terbesar | Momentum")
    print("-" * 96)
    for (pair, bid), trades in sorted(buckets.items()):
        if bid != now_bucket:
            continue
        s = summarize(trades)
        if not s:
            continue
        imb = latest_imb.get(pair, 0.0)
        print(
            f"{pair:8} | {s['buy_ratio'] * 100:9.1f}% | {imb:+9.3f} | "
            f"{s['total_vol']:12,.0f} | {s['max_trade']:14,.0f} | "
            f"{s['change_pct']:+6.2f}%"
        )


def main():
    conn = sqlite3.connect(DB_FILE)
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    print("=== Analisis & Prediksi Statistik ===")
    print(f"Transaksi tersimpan: {count}")
    if count < MIN_DATA:
        print(f"\nData masih sedikit (< {MIN_DATA} transaksi).")
        print("Jalankan monitor.py dulu beberapa jam agar data cukup.")
        print("Analisis dengan data sedikit = kesimpulan tidak valid.")
        return

    data = build_dataset(conn)
    print(f"Window berlabel (5 menit): {len(data)}")
    if len(data) < 50:
        print("Window berlabel masih sedikit (<50). Butuh lebih banyak data.")
        return

    naik = sum(d["up"] for d in data)
    print(f"Historis: {naik}/{len(data)} window mengalami kenaikan "
          f"({naik / len(data) * 100:.1f}%)")

    empirical_table(data)
    imbalance_table(data)
    logistic_eval(data)
    walk_forward_eval(data)
    live_signal(conn)

    # ------------------------------------------------------------------
    # NEW: Spread & Z-Score utilities (for mean-reversion strategy)
    # ------------------------------------------------------------------
    def load_spread_history(conn, pair, lookback_windows=48):
        """Ambil riwayat spread (ask_i - bid_t) dari depth_snapshots + trades."""
        import time as _time
        # Ambil depth snapshots per window 5 menit
        rows = conn.execute(
            "SELECT pair, date, imbalance, bid_vol, ask_vol FROM depth_snapshots "
            "WHERE pair = ? ORDER BY date DESC LIMIT ?",
            (pair, lookback_windows * 2)  # rough limit
        ).fetchall()

        spreads = []
        for pair_r, date, imb, bid_vol, ask_vol in rows:
            # spread approximated dari imbalance * (bid_vol+ask_vol) + reference price
            # Untung nyata butuh price data, disini kita return imbalance saja
            # sebagai proxy untuk mean-reversion signal.
            if imb is not None and (bid_vol + ask_vol) > 0:
                # normalisasi imb ke range -1..+1
                spreads.append(float(imb))
        return spreads


    def calculate_zscore(current_spread, history):
        """Hitung z-score dari current spread vs rolling mean/std history."""
        if not history or len(history) < 2:
            return 0.0
        n = len(history)
        mean = sum(history) / n
        var = sum((x - mean) ** 2 for x in history) / (n - 1)
        std = math.sqrt(max(var, 1e-9))
        if std == 0:
            return 0.0
        z = (current_spread - mean) / std
        return round(z, 4)


    def build_spread_dataset(conn, pairs, lookback_windows=48):
        """Bangun dataset z-score untuk seluruh pair."""
        data = {}
        for pair in pairs:
            spreads = load_spread_history(conn, pair, lookback_windows)
            data[pair] = {
                "spreads": spreads,
                "mean": sum(spreads) / len(spreads) if spreads else 0.0,
                "std": 1.0,  # akan dihitung saat need
            }
        return data


    print("\nPERINGATAN: ini statistik deskriptif, BUKAN sinyal trading.")
    print("Akurasi mendekati 50% menandakan tidak ada keunggulan prediksi.")


if __name__ == "__main__":
    main()
