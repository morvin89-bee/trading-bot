"""
Backtest Walk-Forward Validation untuk Mean-Reversion Z-Score Strategy.

Menggunakan data dari trades.db + depth_snapshots yang sudah kumpul oleh monitor.py.
Strategi: mean reversion berdasarkan z-score spread Indodax vs Tokocrypto.

Penggunaan:
    python backtest_mr.py [--pair btc_idr] [--lookback 48] [--train 200] [--test 100]

Fungsi:
- Bagi data menjadi training (latih mean/std) dan testing (uji sinyal)
- Geser window seterusnya (walk-forward)
- Output: akurasi per fold, baseline (majority class), statistics
- Simpan log sinyal ke signals_backtest.csv
"""

import sqlite3
import math
import csv
import os
import time
from datetime import datetime, timedelta

import scanner
import strategy_mean_reversion as mr
import yaml


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

def load_config():
    with open("config_strategy.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_zscore_from_list(values):
    """Pure Python z-score dari list values (population std)."""
    if not values or len(values) < 2:
        return 0.0, 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n  # population variance
    std = math.sqrt(max(var, 1e-9))
    z = (values[-1] - mean) / std if std else 0.0
    return round(z, 4), mean, std


def build_spread_history(conn, pair, lookback_windows):
    """Ambil history spread dari depth_snapshots."""
    # Ambil imbalance dan volume per window
    rows = conn.execute(
        "SELECT pair, date, imbalance, bid_vol, ask_vol "
        "FROM depth_snapshots WHERE pair = ? ORDER BY date ASC",
        (pair,)
    ).fetchall()
    spreads = []
    for pair_r, date, imb, bid_vol, ask_vol in rows:
        if imb is not None and (bid_vol + ask_vol) > 0:
            # Normalisasi: imbalance sudah -1..+1, simpan langsung
            spreads.append(float(imb))
    # Jika data cukup, kembalikan list; kalau tidak, return list kosong
    return spreads[-lookback_windows:] if len(spreads) >= lookback_windows else spreads


# -----------------------------------------------------------------
# Main backtest class
# -----------------------------------------------------------------

class BacktestMR:
    def __init__(self, pair, train_size=200, test_size=100, lookback=48):
        self.pair = pair
        self.train_size = train_size
        self.test_size = test_size
        self.lookback = lookback
        self.cfg = load_config()

        # Result tracking
        self.folds = []
        self.total_trades = 0
        self.win_trades = 0
        self.daily_pnl = {}

    def run(self, db_path="trades.db"):
        """Jalankan walk-forward backtest."""
        conn = sqlite3.connect(db_path)

        # Ambil data total
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM depth_snapshots WHERE pair = ?", (self.pair,)
        ).fetchone()[0]
        print(f"[Data] Pair: {self.pair}, total depth_snapshots: {total_rows}")

        if total_rows < self.train_size + self.test_size:
            print(f"[Error] Data terlalu sedikit: butuh {self.train_size + self.test_size}, punya {total_rows}")
            conn.close()
            return

        # Urutkan berdasarkan date
        rows = conn.execute(
            "SELECT pair, date, imbalance, bid_vol, ask_vol "
            "FROM depth_snapshots WHERE pair = ? ORDER BY date ASC",
            (self.pair,)
        ).fetchall()

        # Ekstrak spread values (imbalance)
        all_spreads = [float(r[2]) for r in rows if r[2] is not None]

        if len(all_spreads) < self.train_size:
            print(f"[Error] Data spread terlalu sedikit: {len(all_spreads)} < {self.train_size}")
            conn.close()
            return

        # Walk-forward loop
        start = 0
        fold_num = 0
        while start + self.train_size + self.test_size <= len(all_spreads):
            fold_num += 1
            train_spreads = all_spreads[start:start + self.train_size]
            test_spreads = all_spreads[start + self.train_size:start + self.train_size + self.test_size]

            # --- TRAIN: hitung mean/std ---
            train_mean = sum(train_spreads) / len(train_spreads)
            train_var = sum((x - train_mean) ** 2 for x in train_spreads) / len(train_spreads)
            train_std = math.sqrt(max(train_var, 1e-9))

            # --- TEST: generate sinyal dan hitung akurasi ---
            correct = 0
            base_correct = 0  # baseline: selalu prediksi kelas mayoritas di train
            n_train_buy = sum(1 for x in train_spreads if x < 0)  # negative imbalance = dominan jual di Indodax
            n_train_sell = len(train_spreads) - n_train_buy
            majority_class = "buy" if n_train_buy > n_train_sell else "sell"  # klas mayoritas: jika imb negatif, berarti jual dominan
            # Actually baseline: predict harga naik jika di expect mean-reversion
            # Simplifik: baseline = predict arah sama dengan mayoritas imbalance di train
            # Jika majority imb negatif -> harapan harga akan turun (jual), sebaliknya

            # Count majority direction di train
            majority_neg = sum(1 for x in train_spreads if x < 0)  # imb negatif = jual di Indodax
            majority_pos = len(train_spreads) - majority_neg
            # Jika mayoritas imb negatif, artinya order book dominan jual di Indodax
            # Mean reversion: harapan harga akan kembali ke mean (0), jadi prediksi = arah kebalik
            #    jika imb negatif dominan -> harapan: beli (akan naik dari level rendah)
            # Logika: jika imb > 0 (beli dominan) -> mean reversion harap turun jadi jual
            #           jika imb < 0 (jual dominan) -> mean reversion harap naik jadi beli
            # Jadi majority_class untuk prediksi = "buy" kalau mayoritas imb < 0, else "sell"

            pred_buy_dir = "buy" if majority_neg > majority_pos else "sell"
            # baseline: selalu prediksi kebalik dari mayoritas imb
            # Jika mayoritas imb negatif -> prediksi beli (mean reversion: akan naik)
            # Jika mayoritasimb pos -> prediksi jual (akan turun)

            # Test pada setiap window di test set
            for i, current_spread in enumerate(test_spreads):
                idx_in_test = i
                # Hitung z-score vs train mean/std
                z = (current_spread - train_mean) / train_std if train_std else 0.0
                z = round(z, 4)

                # Entry rule mean reversion:
                # Jika z >= +entry_z -> spread terlalu tinggi, harap turun -> SELL (jual Indodax, beli Tokocrypto)
                # Jika z <= -entry_z -> spread terlalu rendah, harap naik -> BUY (beli Indodax, jual Tokocrypto)
                # Exit: abs(z) <= exit_z
                entry_z = self.cfg["strategy"]["entry_z"]
                exit_z = self.cfg["strategy"]["exit_z"]

                # Prediksi:
                # z positif -> spread tinggi -> harapan turun -> prediksi: sell (jual)
                # z negatif -> spread rendah -> harapan naik -> prediksi: buy (beli)
                prediction_buy = z <= -entry_z  # predict beli jika z sangat negatif
                prediction_sell = z >= entry_z  # predict jual jika z sangat positif
                # Ambil prediksi yang aktif
                if prediction_buy:
                    pred_type = "buy"
                elif prediction_sell:
                    pred_type = "sell"
                else:
                    pred_type = "hold"  # tidak ada sinyal kuat

                # Determine "up" (harga akan naik di window berikutnya)
                # Karena kita pakai spread (imbalance), definisi "up" = next spread lebih ekstrem arahnya
                # Atau definisi sederhana: jika next spread > current spread -> up = 1, else = 0
                # Tapi untuk walk-forward sederhana, kita gunakan definisi: next spread 
                # memiliki tanda sama dengan current (mean reversion ke mean = 0)
                # Artinya: kalau current imb > 0, up = 0 (akan turun ke 0); kalau current imb < 0, up = 1 (akan naik ke 0)
                next_idx = start + self.train_size + i + 1
                if next_idx < len(all_spreads):
                    next_spread = all_spreads[next_idx]
                    # up = 1 jika next spread lebih mendekati 0 dari current (mean reversion success)
                    # Jika current < 0 dan next > current (masih negatif tapi kurang negatif) => up=1
                    # Jika current > 0 dan next < current (masih positif tapi kurang positif) => up=0
                    # Atau more simply: up = 1 kalau next_spread memiliki tanda berbeda sama current (lewat mean)
                    # Atau simplest: up = 1 kalau next_spread < 0 (cross ke negatif), else 0
                    # Untuk evaluasi kasus ini: up = 1 kalau next_spread dan current_spread berbeda tanda
                    up = 1 if (current_spread < 0 and next_spread >= 0) or (current_spread > 0 and next_spread <= 0) else 0
                    # Alias: up = 1 berarti "harga akan naik/kembali ke mean", up = 0 berarti "lanjut trending"
                else:
                    up = 0

                # --- Evaluasi prediksi ---
                if pred_type == "buy":
                    # Beli sinyal: harapan up=1 (akan naik)
                    if up == 1:
                        correct += 1
                elif pred_type == "sell":
                    # Jual sinyal: harapan harga turun, artinya up=0 (bukan naik)
                    # Tapi define "win" dari mean reversion: profit jika arah berbalik
                    # Kita define: win jika prediksi kita (buy/sell) menghasilkan profit arah berbalik
                    # Define sederhana: sell prediction win kalau up=0 (harga tidak naik, turun/steady)
                    # Tapi ini ambigu. Maka dari itu kita define:
                    #   - buy prediction: win kalau up=1 (naik dari level rendah)
                    #   - sell prediction: win kalau up=0 (turun dari level tinggi)
                    if up == 0:
                        correct += 1

                # Baseline: selalu prediksi kelas mayoritas di training
                # Jika majority imb negatif -> baseline predict buy
                # Jika majorityimb positif -> baseline predict sell
                if majority_neg > majority_pos:
                    # baseline predict buy
                    if up == 1:
                        base_correct += 1
                else:
                    # baseline predict sell
                    if up == 0:
                        base_correct += 1

            # Simpan hasil fold
            avg_correct = correct / self.test_size * 100 if self.test_size else 0
            avg_base = base_correct / self.test_size * 100 if self.test_size else 0
            self.folds.append({
                "train_start": start,
                "train_end": start + self.train_size,
                "test_start": start + self.train_size,
                "test_end": start + self.train_size + self.test_size,
                "train_mean": train_mean,
                "train_std": train_std,
                "z_at_entry": round((test_spreads[0] - train_mean) / train_std, 4) if train_std else 0,
                "accuracy": avg_correct,
                "base": avg_base,
                "n_test": self.test_size,
                "pred_type_summary": {"buy": 0, "sell": 0, "hold": 0}  # placeholder
            })

            print(f"[Fold {fold_num}] train[{start}:{start+self.train_size}] "
                  f"test[{start+self.train_size}:{start+self.train_size+self.test_size}] | "
                  f"Acc: {avg_correct:.1f}% | Base: {avg_base:.1f}% | n={self.test_size}")

            # Geser ke fold selanjutnya
            start += self.test_size

        # Ringkasan
        print(f"\n=== Walk-Forward Summary ===")
        if not self.folds:
            print("Tidak ada folds yang dibuat (data tidak cukup).")
        else:
            avg_acc = sum(f["accuracy"] for f in self.folds) / len(self.folds)
            avg_base = sum(f["base"] for f in self.folds) / len(self.folds)
            print(f"Rata-rata Akurasi model: {avg_acc:.1f}%")
            print(f"Rata-rata Akurasi Baseline: {avg_base:.1f}%")
            print(f"Model mengalahkan baseline: {avg_acc > avg_base + 3} (perlu >3% margin)")

            # Win rate dari total trades jika di-eksekusi
            total_correct = sum(f["accuracy"] * f["n_test"] / 100 for f in self.folds)
            total_n = sum(f["n_test"] for f in self.folds)
            if total_n > 0:
                overall_acc = total_correct / total_n * 100
                print(f"Akurasi keseluruhan: {overall_acc:.1f}% atas {total_n} uji")

        conn.close()
        return self.folds


# -----------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest Mean-Reversion Z-Score Strategy")
    parser.add_argument("--pair", type=str, default="btc_idr", help="pair to monitor, e.g. btc_idr")
    parser.add_argument("--train", type=int, default=200, help="training window size")
    parser.add_argument("--test", type=int, default=100, help="test window size")
    parser.add_argument("--lookback", type=int, default=48, help="window history untuk z-score")
    parser.add_argument("--db", type=str, default="trades.db", help="path database")
    args = parser.parse_args()

    print(f"Backtest Mean-Reversion Z-Score")
    print(f"Pair: {args.pair} | Train: {args.train} | Test: {args.test} | Lookback: {args.lookback}")
    print(f"DB: {args.db}\n")

    backtest = BacktestMR(
        pair=args.pair,
        train_size=args.train,
        test_size=args.test,
        lookback=args.lookback
    )
    folds = backtest.run(db_path=args.db)


if __name__ == "__main__":
    main()