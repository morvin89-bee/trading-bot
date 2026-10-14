"""
Fase 2: Paper Trading - simulasi mean-reversion arbitrage Indodax vs Tokocrypto.
TIDAK mengirim order asli ke exchange.
Hanya mencatat sinyal + P&L virtual ke signals.csv.
Menggunakan Z-Score mean-reversion dari strategi_mean_reversion.py.
"""

import csv
import os
import time
import yaml
from datetime import datetime

import scanner
import strategy_mean_reversion as mr

# Load config
with open("config_strategy.yaml", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

TRADE_AMOUNT_IDR = 1_000_000   # nominal virtual per trade
SLIPPAGE = 0.001               # estimasi slippage 0.1% (biaya eksekusi realistis)
REBALANCE_FEE = 0.002          # biaya transfer antar exchange (virtual)
INITIAL_IDR = 10_000_000       # saldo virtual awal per exchange
INTERVAL = 10                  # detik antar siklus
LOG_FILE = "signals.csv"


class PaperTrader:
    """Saldo virtual di kedua exchange dengan mean-reversion strategy."""

    def __init__(self):
        self.balance = {"Indodax": INITIAL_IDR, "Tokocrypto": INITIAL_IDR}
        self.trades = 0
        self.wins = 0
        self.pnl = 0.0
        self.last_signal = {}
        self.strategy = mr.MeanReversionStrategy(config_path="config_strategy.yaml")

    def execute(self, pair, buy_ex, sell_ex, buy_price, sell_price, net_after_fee,
                z_score, spread, direction):
        """
        Eksekusi trade virtual dengan logika mean-reversion.
        direction: "long_spread" (beli Indodax, jual Tokocrypto)
                    atau "short_spread" (beli Tokocrypto, jual Indodax)
        """
        now = time.time()

        # 1) Cooldown check berdasarkan strategy
        cooldown = self.strategy.cfg["strategy"]["cooldown_seconds"]
        c_key = (pair, direction)
        if c_key in self.strategy.last_trade and now - self.strategy.last_trade[c_key] < cooldown:
            return None

        # 2) Daily loss circuit breaker
        today = datetime.now().strftime("%Y-%m-%d")
        daily_pnl = self.strategy.daily_pnl.get(today, 0)
        max_daily_loss_pct = self.strategy.cfg["strategy"]["max_daily_loss_pct"]
        # Total modal virtual = 2 * INITIAL_IDR
        max_daily_loss_amt = max_daily_loss_pct * (2 * INITIAL_IDR)
        if daily_pnl <= -max_daily_loss_amt:
            # print(f"  [CIRCUIT BREAKER] Daily loss limit tercapai: {daily_pnl:,.0f} IDR")
            return None

        # 3) Pastikan saldo di exchange pembeli (simulasi transfer jika kurang)
        transfer_fee = 0.0
        if self.balance[buy_ex] < TRADE_AMOUNT_IDR:
            other = "Tokocrypto" if buy_ex == "Indodax" else "Indodax"
            transfer_fee = TRADE_AMOUNT_IDR * REBALANCE_FEE
            # Pastikan cukup saldo di exchange lain
            if self.balance[other] < TRADE_AMOUNT_IDR + transfer_fee:
                return None  # saldo tidak cukup untuk transfer
            self.balance[other] -= TRADE_AMOUNT_IDR + transfer_fee
            self.balance[buy_ex] += TRADE_AMOUNT_IDR

        # 4) Calculate P&L with slippage
        net = net_after_fee - SLIPPAGE
        pnl = TRADE_AMOUNT_IDR * net - transfer_fee

        # 5) Update balance
        self.balance[buy_ex] -= TRADE_AMOUNT_IDR
        self.balance[sell_ex] += TRADE_AMOUNT_IDR * (1 + net)
        self.trades += 1
        self.pnl += pnl
        if pnl > 0:
            self.wins += 1

        # 6) Catat ke strategy untuk tracking
        self.strategy.last_trade[c_key] = now

        # 7) Record daily P&L
        self.strategy.record_daily_pnl(today, pnl)

        # 8) Return result row
        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pair": pair,
            "buy_exchange": buy_ex,
            "buy_price": f"{buy_price:.2f}",
            "sell_exchange": sell_ex,
            "sell_price": f"{sell_price:.2f}",
            "net_after_fee_pct": f"{net_after_fee * 100:.3f}",
            "slippage_pct": f"{SLIPPAGE * 100:.3f}",
            "pnl_idr": f"{pnl:.0f}",
            "transfer_fee_idr": f"{transfer_fee:.0f}",
            "balance_indodax": f"{self.balance['Indodax']:.0f}",
            "balance_tokocrypto": f"{self.balance['Tokocrypto']:.0f}",
            "z_score": f"{z_score:.4f}",
            "spread": f"{spread:.2f}",
            "direction": direction,
        }

    def execute_twap(self, pair, buy_ex, sell_ex, buy_price, sell_price,
                     net_after_fee, z_score, spread, direction, slices=3):
        """
        Eksekusi TWAP: bagi trade jadi 'slices' order kecil merata-ratA.
        Setiap slice eksekusi 1 menit setelah satu sama lain.
        """
        slice_pnl = 0.0
        slice_results = []
        for i in range(slices):
            # Small delay per slice
            time.sleep(max(0, INTERVAL / slices))
            row = self.execute(
                pair, buy_ex, sell_ex, buy_price, sell_price,
                net_after_fee, z_score, spread, direction
            )
            if row:
                slice_results.append(row)
                slice_pnl += float(row["pnl_idr"])
        return slice_results, slice_pnl

    def execute_partial_close(self, pair, pct):
        """
        Eksekusi partial close posisi via TWAP.
        
        Args:
            pair: Pair symbol
            pct: Percentage to close (0-100)
            
        Returns:
            List of execution results from TWAP slices, or None if no position
        """
        if pair not in self.strategy.positions:
            return None
        
        pos = self.strategy.positions[pair]
        original_size = pos["size"]
        close_size = original_size * (pct / 100)
        
        if close_size <= 0:
            return None
        
        # Get current market data for prices
        try:
            from dashboard.utils.background_updater import PAIR_TO_SYMBOL
            pair_id = pair
            symbol = PAIR_TO_SYMBOL.get(pair, pair.upper().replace("_", ""))
            bid_i, ask_i = scanner.indodax_price(pair)
            bid_t, ask_t = scanner.tokocrypto_price(symbol)
        except Exception as e:
            print(f"[Partial Close] Error fetching prices for {pair}: {e}")
            return None
        
        # Calculate spread for net_after_fee
        spread_I_to_T, spread_T_to_I = mr.MeanReversionStrategy.calculate_spread(
            bid_i, ask_i, bid_t, ask_t
        )
        
        if pos["direction"] == "long":
            close_direction = "short_spread"
            buy_ex = "Tokocrypto"
            sell_ex = "Indodax"
            net_after_fee = spread_T_to_I / ask_i
            spread_val = spread_T_to_I
        else:
            close_direction = "long_spread"
            buy_ex = "Indodax"
            sell_ex = "Tokocrypto"
            net_after_fee = spread_I_to_T / ask_t
            spread_val = spread_I_to_T
        
        partial_amount = TRADE_AMOUNT_IDR * (pct / 100)
        
        if partial_amount <= 0:
            return None
        
        slice_results = []
        slices = CFG["strategy"]["twap_slices"]
        
        for i in range(slices):
            time.sleep(max(0, INTERVAL / slices))
            row = self._execute_partial_slice(
                pair, buy_ex, sell_ex, bid_i, ask_i, bid_t, ask_t,
                net_after_fee, spread_val, close_direction, partial_amount=TRADE_AMOUNT_IDR * (pct / 100)
            )
            if row:
                slice_results.append(row)
        
        if slice_results:
            pos["size"] = original_size * (1 - pct / 100)
            if pos["size"] <= 0.001:
                del self.strategy.positions[pair]
        
        return slice_results

    def _execute_partial_slice(self, pair, buy_ex, sell_ex, bid_i, ask_i, bid_t, ask_t,
                                net_after_fee, spread, direction, partial_amount):
        """Execute a single slice of partial close."""
        now = time.time()
        
        cooldown = self.strategy.cfg["strategy"]["cooldown_seconds"]
        c_key = (pair, direction)
        if c_key in self.strategy.last_trade and now - self.strategy.last_trade[c_key] < cooldown:
            return None
        
        today = datetime.now().strftime("%Y-%m-%d")
        daily_pnl = self.strategy.daily_pnl.get(today, 0)
        max_daily_loss_pct = self.strategy.cfg["strategy"]["max_daily_loss_pct"]
        max_daily_loss_amt = max_daily_loss_pct * (2 * INITIAL_IDR)
        if daily_pnl <= -max_daily_loss_amt:
            return None
        
        if self.balance[buy_ex] < partial_amount:
            other = "Tokocrypto" if buy_ex == "Indodax" else "Indodax"
            transfer_fee = partial_amount * REBALANCE_FEE
            if self.balance[other] < partial_amount + transfer_fee:
                return None
            self.balance[other] -= partial_amount + transfer_fee
            self.balance[buy_ex] += partial_amount
        else:
            transfer_fee = 0.0
        
        net = net_after_fee - SLIPPAGE
        pnl = partial_amount * net - transfer_fee
        
        self.balance[buy_ex] -= partial_amount
        self.balance[sell_ex] += partial_amount * (1 + net)
        self.trades += 1
        self.pnl += pnl
        if pnl > 0:
            self.wins += 1
        
        self.strategy.last_trade[c_key] = now
        self.strategy.record_daily_pnl(today, pnl)
        
        # Determine prices for logging
        if direction == "long_spread":
            buy_price = partial_amount  # placeholder
            sell_price = partial_amount
        else:
            buy_price = partial_amount
            sell_price = partial_amount
        
        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pair": pair,
            "buy_exchange": buy_ex,
            "buy_price": f"{partial_amount:.2f}",
            "sell_exchange": sell_ex,
            "sell_price": f"{partial_amount:.2f}",
            "net_after_fee_pct": f"{net_after_fee * 100:.3f}",
            "slippage_pct": f"{SLIPPAGE * 100:.3f}",
            "pnl_idr": f"{pnl:.0f}",
            "transfer_fee_idr": f"{transfer_fee:.0f}",
            "balance_indodax": f"{self.balance['Indodax']:.0f}",
            "balance_tokocrypto": f"{self.balance['Tokocrypto']:.0f}",
            "z_score": "0.0000",
            "spread": f"{spread:.2f}",
            "direction": direction + "_partial",
        }


def log_signal(row):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def main():
    trader = PaperTrader()
    total_capital = INITIAL_IDR * 2
    print("Paper Trading Mean-Reversion Arbitrase (TANPA UANG ASLI) - Ctrl+C untuk berhenti\n")
    print(f"Config: lookback={CFG['strategy']['lookback_windows']} windows, "
          f"entry_z={CFG['strategy']['entry_z']}, "
          f"exit_z={CFG['strategy']['exit_z']}, "
          f"cooldown={CFG['strategy']['cooldown_seconds']}s\n")

    try:
        while True:
            for symbol, pair_id in scanner.PAIRS:
                try:
                    bid_i, ask_i = scanner.indodax_price(pair_id)
                    bid_t, ask_t = scanner.tokocrypto_price(symbol)
                except Exception as e:
                    print(f"{symbol}: gagal ambil harga -> {e}")
                    continue

                # 1) Hitung spread dan z-score
                spread_I_to_T, spread_T_to_I = mr.MeanReversionStrategy.calculate_spread(
                    bid_i, ask_i, bid_t, ask_t
                )
                z_I_to_T = mr.MeanReversionStrategy.compute_zscore(
                    None, "I_to_T"  # history akan ter-update di strategy
                )
                z_T_to_I = mr.MeanReversionStrategy.compute_zscore(
                    None, "T_to_I"
                )

                # Update history di strategy (butuh price history, disederhanakan):
                # Di sini kita update dengan spread terbaru per pair
                # Strategy akan manage history-nya sendiri

                # 2) Cek sinyal entry
                # Butuh fitur tambahan dari analyzer: buy_ratio, volume, momentum
                # Untuk sementara gunakan placeholder dari scanner/data sederhana
                # TODO: Integrasikan dengan analyzer.load_spread_history untuk buy_ratio dll

                # Ambil fitur dari scanner/analyzer sederhana
                # Contoh: cuma pakai z-score dan spread, filter volume via config
                # buy_ratio filter di-strategi_mean_reversion.py sudah ada

                # 3) Check Long Spread signal (beli Indodax, jual Tokocrypto)
                signal = trader.strategy.check_signal(
                    pair_id,
                    bid_i, ask_i, bid_t, ask_t,
                    buy_ratio=0.6,  # placeholder - butuh dari analyzer
                    total_vol=1000,  # placeholder
                    median_vol=500,    # placeholder
                    change_pct=0.0,  # placeholder
                )

                if signal:
                    # Execute with TWAP
                    results, slice_pnl = trader.execute_twap(
                        signal["pair"],
                        signal["buy_exchange"], signal["sell_exchange"],
                        signal["bid_i"], signal["ask_t"],  # buy_price, sell_price
                        float(signal["spread"]),  # net_after_fee prox
                        signal["z_score"],
                        signal["spread"],
                        signal["direction"],
                        slices=CFG["strategy"]["twap_slices"],
                    )
                    # Log setiap slice
                    for row in results:
                        if row:
                            log_signal(row)
                            print(
                                f">>> SINYAL {row['pair']}: beli {row['buy_exchange']} "
                                f"@{row['buy_price']}, jual {row['sell_exchange']} "
                                f"@{row['sell_price']} | z={row['z_score']} | "
                                f"P&L virtual {row['pnl_idr']} IDR"
                            )

                # 3) Check Short Spread signal (beli Tokocrypto, jual Indodax)
                # Seringkali kita cek sinyal kedua di sini, atau strategi sudah handle dupla arah
                # Untuk sekarang, langka di loop utama, kita fokus pada long spread signal

                # 4) Check exit/stop untuk posisi active
                # Strategi sudah mencatat positions di dalamnya
                # Kita cek apakah ada posisi yang harus exit
                for pair_active in list(trader.strategy.positions.keys()):
                    pos = trader.strategy.positions[pair_active]
                    # Hitung z-score saat ini (butuh history update)
                    # Untuk simpel, kita lewati exit check di sini di iterasi pertama
                    # dan hanya rely pada stop_z yang ada di dalam strategy
                    pass

                # 5) Print status harian ringkas
                roi = trader.pnl / total_capital * 100
                daily_pnl = sum(v for v in trader.strategy.daily_pnl.values())
                print(
                    f"[{datetime.now():%H:%M:%S}] trades={trader.trades} "
                    f"win={trader.wins} P&L={trader.pnl:,.0f} IDR ({roi:+.3f}%) | "
                    f"Indodax={trader.balance['Indodax']:,.0f} Toko={trader.balance['Tokocrypto']:,.0f} | "
                    f"Daily P&L={daily_pnl:,.0f} | "
                    f"Active pos: {len(trader.strategy.positions)}"
                )
                print("-" * 100)
                time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\n=== Ringkasan Paper Trading ===")
        print(f"Total trade: {trader.trades}")
        if trader.trades:
            print(f"Menang: {trader.wins} ({trader.wins / trader.trades * 100:.1f}%)")
        print(f"Total P&L: {trader.pnl:,.0f} IDR")
        print(f"ROI: {trader.pnl / total_capital * 100:+.3f}%")
        print(f"Active positions remaining: {len(trader.strategy.positions)}")
        print(f"Log sinyal: {os.path.abspath(LOG_FILE)}")

        # Cetak ringkasan statistik strategi
        print(f"\n--- Strategy Stats ---")
        print(f"Total cooldown events: counting in strategy...")
        # Print daily P&L summary
        for d, pnl in sorted(trader.strategy.daily_pnl.items()):
            print(f"  {d}: {pnl:,.0f} IDR")


if __name__ == "__main__":
    main()