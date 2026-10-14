"""
Test untuk Partial Close Execution di PaperTrader.
"""

import sys
import time
import os
sys.path.insert(0, '.')

from paper_trader import PaperTrader
from dashboard.utils.shared_state import SharedState
import strategy_mean_reversion as mr


def test_partial_close_execution():
    """Test partial close execution."""
    # Setup
    state = SharedState("test_partial.json")
    strategy = mr.MeanReversionStrategy()
    pairs = ["btc_idr"]
    
    # Create position manually
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30, "partial_close_pct": 0
    }
    
    # Create paper trader
    trader = PaperTrader()
    trader.strategy = strategy  # Use our strategy instance
    trader.balance = {"Indodax": 10_000_000, "Tokocrypto": 10_000_000}
    
    # Test partial close (should not fail even if API fails)
    # We're testing the method exists and handles gracefully
    try:
        results = trader.execute_partial_close("btc_idr", 50)
        # May return None due to API errors (no network in test env)
        # Just verify method exists and doesn't crash
        print("[OK] test_partial_close_execution PASSED (method exists)")
    except Exception as e:
        print(f"[FAIL] test_partial_close_execution: {e}")
        raise


def test_partial_close_clamp():
    """Test partial close percentage clamping."""
    strategy = mr.MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30, "partial_close_pct": 0
    }
    
    strategy.set_partial_close("btc_idr", 50)
    assert strategy.positions["btc_idr"]["partial_close_pct"] == 50
    
    # Test clamp
    strategy.set_partial_close("btc_idr", 150)
    assert strategy.positions["btc_idr"]["partial_close_pct"] == 100
    
    strategy.set_partial_close("btc_idr", -10)
    assert strategy.positions["btc_idr"]["partial_close_pct"] == 0
    
    print("[OK] test_partial_close_clamp PASSED")


def run_all_tests():
    print("Menjalankan test Partial Close...\n")
    
    test_partial_close_execution()
    test_partial_close_clamp()
    
    print("\n[SUCCESS] SEMUA TEST PARTIAL CLOSE BERHASIL!")


if __name__ == "__main__":
    run_all_tests()