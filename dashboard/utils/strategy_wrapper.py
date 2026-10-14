"""
Strategy Wrapper - Memudahkan integrasi MeanReversionStrategy ke Streamlit Dashboard.
Membungkus fungsi-fungsi core agar tidak butuh import langsung strategy_mean_reversion
dan bisa digunakan bersama data_loader.
"""

import yaml
import math
from collections import deque

try:
    import strategy_mean_reversion as mr
except ImportError:
    mr = None


# Inisialisasi strategy sekali
_strategy_instance = None


def init_strategy(config_path="config_strategy.yaml"):
    """Inisialisasi MeanReversionStrategy global."""
    global _strategy_instance
    if _strategy_instance is None:
        _strategy_instance = mr.MeanReversionStrategy(config_path=config_path)
    return _strategy_instance


def get_strategy():
    """Ambil instance strategy yang sudah diinisialisasi."""
    return _strategy_instance


def compute_spread(bid_i, ask_i, bid_t, ask_t):
    """Helper: kalkulasi spread kedua arah (seeder strategy_mean_reversion)."""
    if mr:
        return mr.MeanReversionStrategy.calculate_spread(bid_i, ask_i, bid_t, ask_t)
    # Fallback manual
    spread_I_to_T = ask_i - bid_t
    spread_T_to_I = ask_t - bid_i
    return spread_I_to_T, spread_T_to_I


def analyze_pair(pair, bid_i, ask_i, bid_t, ask_t,
                 buy_ratio=0.5, total_vol=0, median_vol=0, change_pct=0.0,
                 strategy=None):
    """
    Analisis satu pair dan kembalikan sinyal/fenomena.
    Returns dict dengan kunci: z_score, spread, signal, filters.
    """
    if strategy is None:
        strategy = get_strategy()

    # 1) Compute spread & z-score
    spread_I_to_T, spread_T_to_I = compute_spread(bid_i, ask_i, bid_t, ask_t)
    z_I_to_T = strategy.compute_zscore(pair, "I_to_T")
    z_T_to_I = strategy.compute_zscore(pair, "T_to_I")

    # 2) Update history di strategy
    strategy.update_history(pair, spread_I_to_T, spread_T_to_I)

    # 3) Cek sinyal entry
    cfg = strategy.cfg["strategy"]
    entry_z = cfg["entry_z"]
    exit_z = cfg["exit_z"]
    stop_z = cfg["stop_z"]
    min_buy_ratio = cfg["min_buy_ratio"]
    min_volume_ratio = cfg["min_volume_ratio"]
    max_momentum = cfg["max_momentum_pct"]

    filters = {
        "buy_ratio_ok": buy_ratio >= min_buy_ratio,
        "volume_ok": (total_vol / median_vol) >= min_volume_ratio if median_vol > 0 else False,
        "momentum_ok": abs(change_pct) <= max_momentum,
    }

    # 4) Entry logic
    signal = None
    if z_I_to_T <= -entry_z and filters["buy_ratio_ok"] and filters["volume_ok"] and filters["momentum_ok"]:
        if pair not in strategy.positions or strategy.positions[pair]["direction"] != "long":
            size = strategy._calc_position_size(pair)
            signal = {
                "type": "long_spread",
                "pair": pair,
                "z_score": z_I_to_T,
                "spread": spread_I_to_T,
                "bid_i": bid_i, "ask_i": ask_i,
                "bid_t": bid_t, "ask_t": ask_t,
                "size": size,
            }
            strategy.last_trade[(pair, "long")] = time.time()
            strategy.positions[pair] = {
                "direction": "long",
                "entry_z": z_I_to_T,
                "entry_time": time.time(),
                "size": size,
            }

    if z_T_to_I >= entry_z and filters["buy_ratio_ok"] and filters["volume_ok"] and filters["momentum_ok"]:
        if pair not in strategy.positions or strategy.positions[pair]["direction"] != "short":
            size = strategy._calc_position_size(pair)
            signal = {
                "type": "short_spread",
                "pair": pair,
                "z_score": z_T_to_I,
                "spread": spread_T_to_I,
                "bid_i": bid_i, "ask_i": ask_i,
                "bid_t": bid_t, "ask_t": ask_t,
                "size": size,
            }
            strategy.last_trade[(pair, "short")] = time.time()
            strategy.positions[pair] = {
                "direction": "short",
                "entry_z": z_T_to_I,
                "entry_time": time.time(),
                "size": size,
            }

    # 5) Exit check
    exit_needed = False
    exit_reason = ""
    if pair in strategy.positions:
        pos = strategy.positions[pair]
        current_z = strategy.compute_zscore(pair, pos["direction"] == "long" and "I_to_T" or "T_to_I")
        # Simplifik: cek abs z vs exit_z
        if abs(current_z) <= exit_z:
            exit_needed = True
            exit_reason = "exit_z_reached"
        elif abs(current_z) >= stop_z:
            exit_needed = True
            exit_reason = "stop_loss"

    return {
        "z_I_to_T": z_I_to_T,
        "z_T_to_I": z_T_to_I,
        "spread_I_to_T": spread_I_to_T,
        "spread_T_to_I": spread_T_to_I,
        "signal": signal,  # dict or None
        "exit_needed": exit_needed,
        "exit_reason": exit_reason,
        "filters": filters,
    }