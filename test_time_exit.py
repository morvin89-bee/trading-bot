"""
Test untuk Time-Based Exit Logic di MeanReversionStrategy.
"""

import sys
import time
sys.path.insert(0, '.')

from strategy_mean_reversion import MeanReversionStrategy


def test_time_exit_triggered():
    """Test time exit triggered setelah waktu yang ditentukan."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time() - 1800,  # 30 menit lalu
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": True, "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    # Harus trigger exit setelah 30 menit
    assert strategy.should_time_exit("btc_idr") == True, "Should exit after 30 minutes"
    
    print("[OK] test_time_exit_triggered PASSED")


def test_time_exit_not_triggered():
    """Test time exit tidak trigger sebelum waktunya."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time() - 600,  # 10 menit lalu
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": True, "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    # Belum waktunya (hanya 10 menit)
    assert strategy.should_time_exit("btc_idr") == False, "Should not exit before 30 minutes"
    
    print("[OK] test_time_exit_not_triggered PASSED")


def test_time_exit_disabled():
    """Test time exit tidak trigger jika disabled."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time() - 3600,  # 1 jam lalu
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    # Disabled - tidak pernah trigger
    assert strategy.should_time_exit("btc_idr") == False, "Should not exit when disabled"
    
    print("[OK] test_time_exit_disabled PASSED")


def test_enable_time_exit():
    """Test enable_time_exit method."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    strategy.enable_time_exit("btc_idr", 45)
    
    pos = strategy.positions["btc_idr"]
    assert pos["time_exit_enabled"] == True
    assert pos["time_exit_minutes"] == 45
    
    print("[OK] test_enable_time_exit PASSED")


def test_set_partial_close():
    """Test set_partial_close method."""
    strategy = MeanReversionStrategy()
    strategy.positions["btc_idr"] = {
        "direction": "long", "entry_z": -2.5, "entry_time": time.time(),
        "size": 0.02, "trailing_stop_active": False, "trailing_stop_z": None,
        "highest_z": -2.5, "lowest_z": None,
        "time_exit_enabled": False, "time_exit_minutes": 30,
        "partial_close_pct": 0,
    }
    
    strategy.set_partial_close("btc_idr", 50)
    
    pos = strategy.positions["btc_idr"]
    assert pos["partial_close_pct"] == 50
    
    # Test clamp
    strategy.set_partial_close("btc_idr", 150)
    assert pos["partial_close_pct"] == 100
    
    strategy.set_partial_close("btc_idr", -10)
    assert pos["partial_close_pct"] == 0
    
    print("[OK] test_set_partial_close PASSED")


def run_all_tests():
    print("Menjalankan test Time Exit...\n")
    
    test_time_exit_triggered()
    test_time_exit_not_triggered()
    test_time_exit_disabled()
    test_enable_time_exit()
    test_set_partial_close()
    
    print("\n[SUCCESS] SEMUA TEST TIME EXIT BERHASIL!")


if __name__ == "__main__":
    run_all_tests()