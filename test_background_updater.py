"""
Test untuk BackgroundUpdater.
"""

import os
import sys
import tempfile
import time
import threading

sys.path.insert(0, '.')

from dashboard.utils.shared_state import SharedState
from dashboard.utils.background_updater import BackgroundUpdater, create_background_updater
import strategy_mean_reversion as mr


def test_background_updater_creation():
    """Test BackgroundUpdater bisa dibuat."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        strategy = mr.MeanReversionStrategy()
        pairs = ["btc_idr", "eth_idr"]
        
        updater = BackgroundUpdater(
            shared_state=state,
            strategy=strategy,
            pairs=pairs,
            interval=1
        )
        
        assert updater is not None
        assert updater.pairs == pairs
        assert updater.interval == 1
        assert not updater.is_running()
        
        print("[OK] test_background_updater_creation PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_background_updater_start_stop():
    """Test start/stop lifecycle."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        strategy = mr.MeanReversionStrategy()
        pairs = ["btc_idr"]
        
        updater = BackgroundUpdater(
            shared_state=state,
            strategy=strategy,
            pairs=pairs,
            interval=1
        )
        
        # Start
        updater.start()
        assert updater.is_running()
        
        # Wait for at least one cycle
        time.sleep(1.5)
        
        # Check data written
        market_data = state.get("market_data", {})
        assert "btc_idr" in market_data or "last_error" in state.get_all()
        
        # Stop
        updater.stop()
        assert not updater.is_running()
        
        print("[OK] test_background_updater_start_stop PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_background_updater_factory():
    """Test factory function create_background_updater."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        strategy = mr.MeanReversionStrategy()
        
        updater = create_background_updater(
            shared_state=state,
            strategy=strategy,
            interval=2
        )
        
        assert updater is not None
        assert len(updater.pairs) > 0  # Should use default scanner.PAIRS
        assert updater.interval == 2
        
        print("[OK] test_background_updater_factory PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_background_updater_market_data_structure():
    """Test struktur market_data yang dihasilkan."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        strategy = mr.MeanReversionStrategy()
        pairs = ["btc_idr"]
        
        updater = BackgroundUpdater(
            shared_state=state,
            strategy=strategy,
            pairs=pairs,
            interval=1
        )
        
        updater.start()
        time.sleep(2)  # Wait for data
        updater.stop()
        
        market_data = state.get("market_data", {})
        
        if "btc_idr" in market_data:
            data = market_data["btc_idr"]
            # Check required fields
            required_fields = [
                "pair", "symbol", "z_I_to_T", "z_T_to_I",
                "spread_I_to_T", "spread_T_to_I",
                "bid_i", "ask_i", "bid_t", "ask_t",
                "signal", "timestamp"
            ]
            for field in required_fields:
                assert field in data, f"Missing field: {field}"
            
            # Check types
            assert isinstance(data["z_I_to_T"], (int, float))
            assert isinstance(data["z_T_to_I"], (int, float))
            assert isinstance(data["spread_I_to_T"], (int, float))
            assert isinstance(data["spread_T_to_I"], (int, float))
            
            print("[OK] test_background_updater_market_data_structure PASSED")
        else:
            # If no data (API error), check last_error exists
            all_state = state.get_all()
            assert "last_error" in all_state
            print("[OK] test_background_updater_market_data_structure PASSED (with error handling)")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def run_all_tests():
    """Jalankan semua test."""
    print("Menjalankan test BackgroundUpdater...\n")
    
    test_background_updater_creation()
    test_background_updater_start_stop()
    test_background_updater_factory()
    test_background_updater_market_data_structure()
    
    print("\n[SUCCESS] SEMUA TEST BACKGROUND UPDATER BERHASIL!")


if __name__ == "__main__":
    import os
    run_all_tests()