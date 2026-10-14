"""
Mean Reversion Z-Score Strategy for Indodax vs Tokocrypto Arbitrage.
Menggunakan spread antara dua exchange dan mean-reversion dengan z-score.
Timeframe: 5 menit per window (sesuai WINDOW_SEC=300).
"""

import yaml
import math
from collections import deque
from datetime import datetime


class MeanReversionStrategy:
    """Core strategy logic for mean reversion arbitrage."""

    def __init__(self, config_path="config_strategy.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

        self.lookback = self.cfg["strategy"]["lookback_windows"]
        self.entry_z = self.cfg["strategy"]["entry_z"]
        self.exit_z = self.cfg["strategy"]["exit_z"]
        self.stop_z = self.cfg["strategy"]["stop_z"]

        # Rolling history per pair: {pair: deque of spreads}
        self.spread_history = {}

        # Active positions: {pair: {"direction": "long"|"short", "entry_z": float, "entry_time": float, "size": float,
        #   "trailing_stop_active": bool, "trailing_stop_z": float, "highest_z": float, "lowest_z": float,
        #   "time_exit_enabled": bool, "time_exit_minutes": int, "partial_close_pct": int}}
        self.positions = {}

        # Daily P&L tracking: {date_str: pnl_idr}
        self.daily_pnl = {}

        # Cooldown tracking: {pair: {direction: last_trade_time}}
        self.last_trade = {}

    # ------------------------------------------------------------------
    # Spread calculation
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_spread(bid_i, ask_i, bid_t, ask_t):
        """
        Calculate both directional spreads.
        Returns (spread_I_to_T, spread_T_to_I)
        spread_I_to_T = ask_indodax - bid_tokocrypto  (beli Indodax, jual Tokocrypto)
        spread_T_to_I = ask_tokocrypto - bid_indodax  (beli Tokocrypto, jual Indodax)
        """
        spread_I_to_T = ask_i - bid_t
        spread_T_to_I = ask_t - bid_i
        return spread_I_to_T, spread_T_to_I

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def update_history(self, pair, spread_I_to_T, spread_T_to_I):
        """Append latest spreads to rolling history (deque)."""
        if pair not in self.spread_history:
            self.spread_history[pair] = {
                "I_to_T": deque(maxlen=self.lookback),
                "T_to_I": deque(maxlen=self.lookback),
            }
        self.spread_history[pair]["I_to_T"].append(spread_I_to_T)
        self.spread_history[pair]["T_to_I"].append(spread_T_to_I)

    @staticmethod
    def _mean_std(history):
        """Calculate mean and std dari list values (pure Python, no numpy)."""
        if not history:
            return 0.0, 1.0
        n = len(history)
        mean = sum(history) / n
        if n < 2:
            return mean, 1.0
        var = sum((x - mean) ** 2 for x in history) / (n - 1)
        std = math.sqrt(max(var, 1e-9))  # menghindari divide by zero
        return mean, std

    def compute_zscore(self, pair, direction="I_to_T"):
        """
        Hitung z-score aktual vs rolling mean/std history.
        direction: "I_to_T" atau "T_to_I"
        Returns z-score (float). Jika history kurang dari 2: return 0.0.
        """
        if pair not in self.spread_history:
            return 0.0

        hist = self.spread_history[pair][direction]
        if len(hist) < 2:
            return 0.0

        mean, std = self._mean_std(list(hist))
        if std == 0:
            return 0.0

        current = hist[-1]  # nilai terakhir (sekarang)
        z = (current - mean) / std
        return round(z, 4)

    # ------------------------------------------------------------------
    # Filter checks (from analyzer.py features)
    # ------------------------------------------------------------------

    def _check_buy_ratio(self, buy_ratio):
        return buy_ratio >= self.cfg["strategy"]["min_buy_ratio"]

    def _check_volume_ratio(self, total_vol, median_vol):
        ratio = (total_vol / median_vol) if median_vol > 0 else 0
        return ratio >= self.cfg["strategy"]["min_volume_ratio"]

    def _check_momentum(self, change_pct):
        """Block jika momentum terlalu kuat (prevent catch falling knife)."""
        return abs(change_pct) <= self.cfg["strategy"]["max_momentum_pct"]

    # ------------------------------------------------------------------
    # Signal logic
    # ------------------------------------------------------------------

    def check_signal(self, pair, bid_i, ask_i, bid_t, ask_t,
                     buy_ratio, total_vol, median_vol, change_pct):
        """
        Cek sinyal entry Long Spread (beli Indodax, jual Tokocrypto)
        atau Short Spread (beli Tokocrypto, jual Indodax).
        Returns dict sinyal or None.
        """

        # 1) Cooldown check
        now = datetime.now().timestamp()
        cooldown = self.cfg["strategy"]["cooldown_seconds"]
        c_key = (pair, "long")
        if c_key in self.last_trade and now - self.last_trade[c_key] < cooldown:
            return None
        c_key = (pair, "short")
        if c_key in self.last_trade and now - self.last_trade[c_key] < cooldown:
            return None

        # 2) Update spread history
        spread_I_to_T, spread_T_to_I = self.calculate_spread(bid_i, ask_i, bid_t, ask_t)
        self.update_history(pair, spread_I_to_T, spread_T_to_I)

        # 3) Compute z-scores
        z_I_to_T = self.compute_zscore(pair, "I_to_T")
        z_T_to_I = self.compute_zscore(pair, "T_to_I")

        # 4) Filter checks
        if not self._check_buy_ratio(buy_ratio):
            return None
        if not self._check_volume_ratio(total_vol, median_vol):
            return None
        if not self._check_momentum(change_pct):
            return None

        # 5) Entry Long Spread: z_I_to_T <= -entry_z
        if z_I_to_T <= -self.entry_z:
            # Pastikan tidak punya posisi long yang masih active
            if pair not in self.positions or self.positions[pair]["direction"] != "long":
                size = self._calc_position_size(pair)
                if size <= 0:
                    return None
                self.positions[pair] = {
                    "direction": "long",
                    "entry_z": z_I_to_T,
                    "entry_time": now,
                    "size": size,
                    "trailing_stop_active": False,
                    "trailing_stop_z": None,
                    "highest_z": z_I_to_T,
                    "lowest_z": None,
                    "time_exit_enabled": False,
                    "time_exit_minutes": 30,
                    "partial_close_pct": 0,
                }
                self.last_trade[(pair, "long")] = now
                return {
                    "type": "long_spread",
                    "pair": pair,
                    "z_score": z_I_to_T,
                    "spread": spread_I_to_T,
                    "bid_i": bid_i, "ask_i": ask_i,
                    "bid_t": bid_t, "ask_t": ask_t,
                    "size": size,
                }

        # 6) Entry Short Spread: z_T_to_I >= +entry_z
        if z_T_to_I >= self.entry_z:
            if pair not in self.positions or self.positions[pair]["direction"] != "short":
                size = self._calc_position_size(pair)
                if size <= 0:
                    return None
                self.positions[pair] = {
                    "direction": "short",
                    "entry_z": z_T_to_I,
                    "entry_time": now,
                    "size": size,
                    "trailing_stop_active": False,
                    "trailing_stop_z": None,
                    "highest_z": None,
                    "lowest_z": z_T_to_I,
                    "time_exit_enabled": False,
                    "time_exit_minutes": 30,
                    "partial_close_pct": 0,
                }
                self.last_trade[(pair, "short")] = now
                return {
                    "type": "short_spread",
                    "pair": pair,
                    "z_score": z_T_to_I,
                    "spread": spread_T_to_I,
                    "bid_i": bid_i, "ask_i": ask_i,
                    "bid_t": bid_t, "ask_t": ask_t,
                    "size": size,
                }

        return None

    # ------------------------------------------------------------------
    # Exit / stop logic
    # ------------------------------------------------------------------

    def should_exit(self, pair, current_z, current_spread):
        """
        Kembalikan True kalau posisi harus ditutup (exit atau stop).
        """
        if pair not in self.positions:
            return False

        pos = self.positions[pair]
        direction = pos["direction"]
        entry_z = pos["entry_z"]

        exit_z = self.exit_z
        stop_z = self.stop_z

        exit_signal = False

        if direction == "long":
            # Exit jika z-score kembali ke vicinity mean (|z| <= exit_z)
            if abs(current_z) <= exit_z:
                exit_signal = True
            # Stop loss jika z-score naik ekstrem (trend jadi)
            if current_z >= stop_z:
                exit_signal = True  # forced close

        elif direction == "short":
            # Exit jika z-score kembali ke vicinity mean
            if abs(current_z) <= exit_z:
                exit_signal = True
            # Stop loss jika z-score turun ekstrem
            if current_z <= -stop_z:
                exit_signal = True  # forced close

        if exit_signal:
            # Hapus posisi
            del self.positions[pair]
        return exit_signal

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _calc_position_size(self, pair):
        """Calculate position size based on % of modal harian s.d. max_concurrent."""
        # Simpel: fixed % dari INITIAL_IDR per trade, tapi limit exposure per pair
        base_size_pct = self.cfg["strategy"]["position_size_pct"]  # 2% per trade
        max_pct = self.cfg["strategy"]["max_position_pct"]  # 10% per pair

        # Cek exposure harian per pair (simulasi: ambil dari balance terbaru)
        # Di sini kita pakai nominal tetap % dari modal virtual
        size_idr = base_size_pct  # akan dikalikan di paper_trader.py
        return size_idr if size_idr <= max_pct else max_pct

    # ------------------------------------------------------------------
    # Daily P&L tracking
    # ------------------------------------------------------------------

    def record_daily_pnl(self, date_str, pnl_idr):
        """Catat P&L harian untuk circuit breaker."""
        self.daily_pnl[date_str] = self.daily_pnl.get(date_str, 0) + pnl_idr

    def check_daily_limit(self, current_pnl_idr):
        """Kembalikan True jika hari ini masih dalam limit loss."""
        today = datetime.now().strftime("%Y-%m-%d")
        daily_loss = self.daily_pnl.get(today, 0)
        max_loss_pct = self.cfg["strategy"]["max_daily_loss_pct"]
        # Asumsi INITIAL_IDR total = 2 * 1_000_000 dari paper_trader
        # Jadi max loss harian = max_loss_pct * 2_000_000
        # Tapi kita cuma tracking relatif:
        return abs(daily_loss) < (max_loss_pct * 2_000_000)  # placeholder count

    # ------------------------------------------------------------------
    # Trailing Stop & Time Exit Logic (Enhanced Take Profit)
    # ------------------------------------------------------------------

    def _init_trailing_fields(self, pos, direction):
        """Initialize trailing stop fields for a position if not present."""
        if "trailing_stop_active" not in pos:
            pos["trailing_stop_active"] = False
        if "trailing_stop_z" not in pos:
            pos["trailing_stop_z"] = None
        if direction == "long":
            if "highest_z" not in pos:
                pos["highest_z"] = pos.get("entry_z", 0)
        else:  # short
            if "lowest_z" not in pos:
                pos["lowest_z"] = pos.get("entry_z", 0)
        if "time_exit_enabled" not in pos:
            pos["time_exit_enabled"] = False
        if "time_exit_minutes" not in pos:
            pos["time_exit_minutes"] = 30
        if "partial_close_pct" not in pos:
            pos["partial_close_pct"] = 0

    def update_trailing_stop(self, pair, current_z):
        """
        Update trailing stop level when price moves favorably.
        For long: track highest_z, move trailing_stop_z up when z improves
        For short: track lowest_z, move trailing_stop_z down when z improves
        """
        if pair not in self.positions:
            return
        
        pos = self.positions[pair]
        direction = pos["direction"]
        self._init_trailing_fields(pos, direction)
        
        if not pos["trailing_stop_active"]:
            return
        
        if direction == "long":
            # Long: favorable = z increases (less negative or positive)
            if current_z > pos["highest_z"]:
                pos["highest_z"] = current_z
                # Move trailing stop up: trail by buffer from highest_z
                # Buffer = entry_z threshold (e.g., 2.0) 
                buffer = self.entry_z
                new_trailing = current_z - buffer
                if pos["trailing_stop_z"] is None or new_trailing > pos["trailing_stop_z"]:
                    pos["trailing_stop_z"] = new_trailing
        else:  # short
            # Short: favorable = z decreases (more negative)
            if current_z < pos["lowest_z"]:
                pos["lowest_z"] = current_z
                buffer = self.entry_z
                new_trailing = current_z + buffer
                if pos["trailing_stop_z"] is None or new_trailing < pos["trailing_stop_z"]:
                    pos["trailing_stop_z"] = new_trailing

    def check_trailing_stop(self, pair, current_z):
        """
        Check if trailing stop is hit.
        Returns True if position should be closed due to trailing stop.
        """
        if pair not in self.positions:
            return False
        
        pos = self.positions[pair]
        direction = pos["direction"]
        self._init_trailing_fields(pos, pos["direction"])
        
        if not pos["trailing_stop_active"] or pos["trailing_stop_z"] is None:
            return False
        
        if direction == "long":
            # Long: exit if z drops below trailing_stop_z
            return current_z < pos["trailing_stop_z"]
        else:  # short
            # Short: exit if z rises above trailing_stop_z
            return current_z > pos["trailing_stop_z"]

    def should_time_exit(self, pair):
        """
        Check if time-based exit should trigger for position.
        Returns True if position should be closed due to time limit.
        """
        if pair not in self.positions:
            return False
        
        pos = self.positions[pair]
        if not pos.get("time_exit_enabled", False):
            return False
        
        elapsed_minutes = (datetime.now().timestamp() - pos["entry_time"]) / 60
        return elapsed_minutes >= pos.get("time_exit_minutes", 30)

    def activate_trailing_stop(self, pair):
        """Activate trailing stop for a position."""
        if pair in self.positions:
            pos = self.positions[pair]
            self._init_trailing_fields(pos, pos["direction"])
            pos["trailing_stop_active"] = True
            # Initialize trailing stop at entry threshold
            if pos["direction"] == "long":
                pos["trailing_stop_z"] = pos["entry_z"] - self.entry_z
                pos["highest_z"] = pos["entry_z"]
            else:
                pos["trailing_stop_z"] = pos["entry_z"] + self.entry_z
                pos["lowest_z"] = pos["entry_z"]

    def enable_time_exit(self, pair, minutes=30):
        """Enable time-based exit for a position."""
        if pair in self.positions:
            pos = self.positions[pair]
            pos["time_exit_enabled"] = True
            pos["time_exit_minutes"] = minutes

    def set_partial_close(self, pair, pct):
        """Set partial close percentage for a position."""
        if pair in self.positions:
            self.positions[pair]["partial_close_pct"] = max(0, min(100, pct))