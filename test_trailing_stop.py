"""
Test untuk Trailing Stop Logic di MeanReversionStrategy.
"""

import sys
import time
sys.path.insert(0, '.')

from strategy_mean_reversion import MeanReversionStrategy


def test_trailing_stop_activation():
    """Test trailing stop aktivasi saat z bergerak favorable."""
    strategy = MeanReversionStrategy()
    
    # Simulasi entry position long
    strategy.positions["btc_idr"] = {
        "direction": "long",
        "entry_z": -2.5,
        "entry_time": time.time(),
        "size": 0.02,
        "trailing_stop_active": False,
        "trailing_stop_z": None,
        "highest_z": -2.5,
        "lowest_z": None,
        "time_exit_enabled": False,
        "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    # Aktifkan trailing stop terlebih dahulu (manual activation)
    strategy.activate_trailing_stop("btc_idr")
    
    # Z bergerak favorable dari -2.5 ke -1.0 (long position improving)
    strategy.update_trailing_stop("btc_idr", current_z=-1.0)
    
    # Trailing stop harus aktif dan track highest_z
    pos = strategy.positions["btc_idr"]
    assert pos["trailing_stop_active"] == True, "Trailing stop should be active"
    assert pos["trailing_stop_z"] is not None, "Trailing stop z should be set"
    assert pos["highest_z"] == -1.0, "Should track highest z"
    
    print("[OK] test_trailing_stop_activation PASSED")


def test_trailing_stop_tracks_favorable_movement():
    """Test trailing stop melacak pergerakan favorable."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": True, "trailing_stop_z": -4.5,  # entry_z - buffer = -2.5 - 2.0
        "highest_z": -1.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30, "partial_close_pct": 0
    }
    
    # Z continue improve ke -0.8
    strategy.update_trailing_stop("btc_idr", current_z=-0.8)
    
    pos = strategy.positions["btc_idr"]
    # Trailing stop harus move up (less negative = better untuk long)
    # highest_z = -0.8, trailing = -0.8 - 2.0 = -2.8 > -4.5
    assert pos["trailing_stop_z"] > -4.5, "Trailing stop should move up from -4.5"
    assert pos["highest_z"] == -0.8, "Should update highest_z"
    
    print("[OK] test_trailing_stop_tracks_favorable_movement PASSED")


def test_trailing_stop_triggers_exit():
    """Test trailing stop trigger exit saat reversal hit."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": True, "trailing_stop_z": -1.0,
        "highest_z": -0.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30, "partial_close_pct": 0
    }
    
    # Z reverse dan hit trailing stop (-1.1 < -1.0)
    should_exit = strategy.check_trailing_stop("btc_idr", current_z=-1.1)
    assert should_exit == True, "Should exit when z hits trailing stop"
    
    # Z masih favorable - no exit
    should_exit = strategy.check_trailing_stop("btc_idr", current_z=-0.8)
    assert should_exit == False, "Should not exit when z still favorable"
    
    print("[OK] test_trailing_stop_triggers_exit PASSED")


def test_trailing_stop_short_position():
    """Test trailing stop untuk short position (inverse logic)."""
    strategy = MeanReversionStrategy()
    strategy.positions["eth_idr"] = {
        "direction": "short", "entry_z": 2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": True, "trailing_stop_z": 3.0,  # after moving favorably
        "highest_z": None, "lowest_z": 1.0,
        "time_exit_enabled": False, "time_exit_minutes": 30, "partial_close_pct": 0
    }
    
    # Z reverse naik ke 3.5 (hit trailing stop untuk short)
    should_exit = strategy.check_trailing_stop("eth_idr", current_z=3.5)
    assert should_exit == True, "Should exit when z rises above trailing stop"
    
    # Z masih favorable - no exit
    should_exit = strategy.check_trailing_stop("eth_idr", current_z=0.5)
    assert should_exit == False, "Should not exit when z still favorable"
    
    print("[OK] test_trailing_stop_short_position PASSED")


def run_all_tests():
    print("Menjalankan test Trailing Stop...\n")
    
    test_trailing_stop_activation()
    test_trailing_stop_tracks_favorable_movement()
    test_trailing_stop_triggers_exit()
    test_trailing_stop_short_position()
    
    print("\n[SUCCESS] SEMUA TEST TRAILING STOP BERHASIL!")


if __name__ == "__main__":
    run_all_tests()