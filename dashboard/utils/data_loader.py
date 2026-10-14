"""
Data Loader untuk Streamlit Dashboard.
Bertugas: load data dari trades.db + depth_snapshots + signals.csv,
hitung z-score, dan siapkan untuk strategy wrapper.
Semua fungsi thread-safe (dapat dipanggil dari background thread).
"""

import csv
import sqlite3
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta

import yaml

# Inisialisasi global (akan di-set saat app dimulai)
CFG = None
DB_PATH = "trades.db"
SIGNALS_PATH = "signals.csv"
STRATEGY = None  # MeanReversionStrategy instance


def init_config():
    """Memuat config_strategy.yaml sekali saat startup."""
    global CFG
    if CFG is None:
        with open("config_strategy.yaml", encoding="utf-8") as f:
            CFG = yaml.safe_load(f)


def get_config():
    """Ambil config yang sudah diinisialisasi."""
    return CFG


def get_spread_history(conn, pair, lookback_windows):
    """
    Ambil history imbalance dari depth_snapshots.
    Returns list float [-1.0 .. +1.0].
    """
    try:
        rows = conn.execute(
            "SELECT pair, date, imbalance, bid_vol, ask_vol "
            "FROM depth_snapshots WHERE pair = ? ORDER BY date ASC",
            (pair,)
        ).fetchall()
        spreads = []
        for pair_r, date, imb, bid_vol, ask_vol in rows:
            if imb is not None and (bid_vol + ask_vol) > 0:
                spreads.append(float(imb))
        # Return terbaru sampai lookback_windows
        return spreads[-lookback_windows:] if len(spreads) >= lookback_windows else spreads
    except Exception as e:
        print(f"[data_loader] Error fetch spread {pair}: {e}")
        return []


def compute_zscore(current_spread, history):
    """
    Hitung z-score aktual vs rolling mean/std history (pure Python).
    Returns (z, mean, std).
    """
    if not history or len(history) < 2:
        return 0.0, 0.0, 1.0
    n = len(history)
    mean = sum(history) / n
    var = sum((x - mean) ** 2 for x in history) / n  # population variance
    std = math.sqrt(max(var, 1e-9))
    z = (current_spread - mean) / std if std else 0.0
    return round(z, 4), round(mean, 6), round(std, 6)


def load_signals_recent(hours=24):
    """
    Load sinyal terbaru dari signals.csv per interval hours.
    Returns list dict.
    """
    if not os.path.exists(SIGNALS_PATH):
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    signals = []
    try:
        with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
            header = f.readline()  # skip header
            for line in f:
                try:
                    row = dict(csv.DictReader([line]))
                    ts_str = row.get("time", "")
                    if ts_str:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            signals.append(row)
                except Exception:
                    continue
    except Exception as e:
        print(f"[data_loader] Error read signals: {e}")
    return signals


def load_signals_all():
    """Load semua baris signals.csv (kecuali header)."""
    if not os.path.exists(SIGNALS_PATH):
        return []
    signals = []
    try:
        with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
            header = f.readline()
            for line in f:
                try:
                    row = dict(csv.DictReader([line]))
                    signals.append(row)
                except Exception:
                    continue
    except Exception as e:
        print(f"[data_loader] Error read all signals: {e}")
    return signals


def get_active_positions(strategy_instance):
    """
    Ambil posisi aktif dari MeanReversionStrategy instance.
    Returns dict {pair: {direction, entry_z, entry_time, size}}.
    """
    if strategy_instance is None:
        return {}
    return getattr(strategy_instance, "positions", {})


def get_daily_pnl(strategy_instance):
    """Ambil daily P&L dict dari strategy."""
    if strategy_instance is None:
        return {}
    return getattr(strategy_instance, "daily_pnl", {})


def get_balance_summary():
    """Ambil summary balance dari signals.csv last row."""
    if not os.path.exists(SIGNALS_PATH):
        return {"total_pnl": 0, "trades": 0, "win_rate": 0}
    try:
        with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) < 2:
                return {"total_pnl": 0, "trades": 0, "win_rate": 0}
            last_row = dict(csv.DictReader([lines[-1]]))
            total_pnl = float(last_row.get("pnl_idr", 0))
            trades = int(last_row.get("trades", 0)) if last_row.get("trades") else 0
            win_rate = 0
            if trades > 0:
                wins = sum(1 for line in lines[1:] if line.strip() and float(dict(csv.DictReader([line])).get("pnl_idr", 0)) > 0)
                win_rate = wins / trades * 100
            return {"total_pnl": total_pnl, "trades": trades, "win_rate": win_rate}
    except Exception as e:
        print(f"[data_loader] Error balance summary: {e}")
        return {"total_pnl": 0, "trades": 0, "win_rate": 0}


def get_tailscale_ip():
    """Ambil IP Tailscale dari environment/system."""
    try:
        import subprocess
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass
    # Fallback: cek env
    return os.environ.get("TAILSCALE_IP", "N/A")


# ============================================================
# REAL Z-SCORE FUNCTIONS (untuk integrasi database real)
# ============================================================

def get_latest_imbalance_history(conn, pair: str, lookback: int = 48) -> list:
    """
    Ambil history imbalance terbaru dari depth_snapshots.
    
    Args:
        conn: SQLite connection
        pair: Pair symbol (e.g., 'btc_idr')
        lookback: Jumlah window lookback (default 48 = 4 jam)
        
    Returns:
        List imbalance values (chronological order, oldest first)
    """
    try:
        rows = conn.execute(
            "SELECT imbalance FROM depth_snapshots WHERE pair = ? ORDER BY date DESC LIMIT ?",
            (pair, lookback)
        ).fetchall()
        # Reverse to chronological order (oldest first)
        imbalances = [float(r[0]) for r in rows if r[0] is not None]
        return imbalances[::-1]
    except Exception as e:
        print(f"[data_loader] Error fetch imbalance history {pair}: {e}")
        return []


def compute_zscore_from_history(current_value: float, history: list) -> tuple:
    """
    Hitung z-score dari current value vs history (pure Python).
    Menggunakan population variance (seperti backtest_mr.py).
    
    Args:
        current_value: Nilai current (terbaru)
        history: List nilai historis (chronological order)
        
    Returns:
        (z_score, mean, std)
    """
    if not history or len(history) < 2:
        return 0.0, 0.0, 1.0
    
    n = len(history)
    mean = sum(history) / n
    var = sum((x - mean) ** 2 for x in history) / n  # population variance
    std = math.sqrt(max(var, 1e-9))
    z = (current_value - mean) / std if std else 0.0
    return round(z, 4), round(mean, 6), round(std, 6)


def get_real_zscore_db(conn, pair: str, strategy, lookback: int = 48) -> tuple:
    """
    Hitung z-score real dari database menggunakan strategy's rolling window.
    
    Args:
        conn: SQLite connection
        pair: Pair symbol
        strategy: MeanReversionStrategy instance
        lookback: Lookback windows
        
    Returns:
        (z_I_to_T, z_T_to_I, mean_I, mean_T, std_I, std_T)
    """
    # Get imbalance history
    history = get_latest_imbalance_history(conn, pair, lookback)
    
    if len(history) < 2:
        return 0.0, 0.0, 0.0, 0.0, 1.0, 1.0
    
    # Strategy maintains its own rolling window
    # Just get current z-scores from strategy
    z_I = strategy.compute_zscore(pair, "I_to_T")
    z_T = strategy.compute_zscore(pair, "T_to_I")
    
    # Calculate mean/std for display
    current_I = history[-1]
    current_T = -history[-1]  # inverse for T_to_I
    
    mean_I = sum(history) / len(history)
    mean_T = -mean_I
    
    var_I = sum((x - mean_I) ** 2 for x in history) / len(history)
    std_I = math.sqrt(max(var_I, 1e-9))
    std_T = std_I  # same magnitude
    
    return z_I, z_T, mean_I, mean_T, std_I, std_T


def load_market_data_for_dashboard(conn, strategy, pairs: list) -> dict:
    """
    Load semua data market untuk dashboard dari database.
    
    Args:
        conn: SQLite connection (check_same_thread=False)
        strategy: MeanReversionStrategy instance
        pairs: List pair symbols
        
    Returns:
        Dict {pair: {z_I, z_T, spread_I, spread_T, signal, prices...}}
    """
    import scanner
    import strategy_mean_reversion as mr
    
    PAIR_TO_SYMBOL = {
        "btc_idr": "BTCIDR", "eth_idr": "ETHIDR", "usdt_idr": "USDTIDR",
        "bnb_idr": "BNBIDR", "doge_idr": "DOGEIDR", "ada_idr": "ADAIDR",
        "xrp_idr": "XRPIDR", "sol_idr": "SOLIDR", "avax_idr": "AVAXIDR",
        "arb_idr": "ARBIDR", "ondo_idr": "ONDOIDR", "hbar_idr": "HBARIDR",
        "wld_idr": "WLDIDR",
    }
    
    market_data = {}
    
    for pair in pairs:
        try:
            symbol = PAIR_TO_SYMBOL.get(pair)
            if not symbol:
                continue
                
            # Get prices from exchanges
            bid_i, ask_i = scanner.indodax_price(pair)
            bid_t, ask_t = scanner.tokocrypto_price(PAIR_TO_SYMBOL[pair])
            
            # Calculate spreads
            spread_I, spread_T = mr.MeanReversionStrategy.calculate_spread(
                bid_i, ask_i, bid_t, ask_t
            )
            
            # Update strategy history
            strategy.update_history(pair, spread_I, spread_T)
            
            # Get z-scores from strategy
            z_I = strategy.compute_zscore(pair, "I_to_T")
            z_T = strategy.compute_zscore(pair, "T_to_I")
            
            # Check signal (with permissive defaults for now)
            signal = strategy.check_signal(
                pair=pair,
                bid_i=bid_i, ask_i=ask_i,
                bid_t=bid_t, ask_t=ask_t,
                buy_ratio=0.6,
                total_vol=1000,
                median_vol=500,
                change_pct=0.0
            )
            
            market_data[pair] = {
                "pair": pair,
                "symbol": symbol,
                "z_I_to_T": round(z_I, 4),
                "z_T_to_I": round(z_T, 4),
                "spread_I_to_T": round(spread_I, 2),
                "spread_T_to_I": round(spread_T, 2),
                "bid_i": bid_i,
                "ask_i": ask_i,
                "bid_t": bid_t,
                "ask_t": ask_t,
                "signal": signal,
                "timestamp": time.time(),
            }
            
        except Exception as e:
            market_data[pair] = {"error": str(e)}
    
    return market_data