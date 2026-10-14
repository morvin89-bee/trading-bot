"""
Test untuk SharedState thread-safe implementation.
"""

import os
import tempfile
import threading
import time
import json

from dashboard.utils.shared_state import SharedState


def test_shared_state_basic():
    """Test basic get/set operations."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        
        # Test set and get
        state.set("running", True)
        assert state.get("running") == True, "Failed to get boolean value"
        
        state.set("running", False)
        assert state.get("running") == False, "Failed to update boolean value"
        
        # Test default value
        assert state.get("nonexistent", "default") == "default", "Default value failed"
        
        # Test various types
        state.set("number", 42)
        state.set("string", "hello")
        state.set("list", [1, 2, 3])
        state.set("dict", {"a": 1, "b": 2})
        
        assert state.get("number") == 42
        assert state.get("string") == "hello"
        assert state.get("list") == [1, 2, 3]
        assert state.get("dict") == {"a": 1, "b": 2}
        
        print("[OK] test_shared_state_basic PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_update():
    """Test atomic update of multiple keys."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        
        state.update({"a": 1, "b": 2, "c": 3})
        
        assert state.get("a") == 1
        assert state.get("b") == 2
        assert state.get("c") == 3
        
        # Update existing + add new
        state.update({"b": 20, "d": 4})
        
        assert state.get("a") == 1  # unchanged
        assert state.get("b") == 20  # updated
        assert state.get("c") == 3  # unchanged
        assert state.get("d") == 4  # new
        
        print("[OK] test_shared_state_update PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_callbacks():
    """Test callback notifications."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        notifications = []
        
        def callback(key, value):
            notifications.append((key, value))
        
        state.subscribe(callback)
        
        state.set("key1", "value1")
        state.set("key2", "value2")
        state.update({"key3": "value3", "key4": "value4"})
        
        # Wait a bit for callbacks
        time.sleep(0.05)
        
        assert len(notifications) == 4, f"Expected 4 notifications, got {len(notifications)}"
        assert ("key1", "value1") in notifications
        assert ("key2", "value2") in notifications
        assert ("key3", "value3") in notifications
        assert ("key4", "value4") in notifications
        
        # Test unsubscribe
        notifications.clear()
        state.unsubscribe(callback)
        state.set("key5", "value5")
        time.sleep(0.05)
        assert len(notifications) == 0, "Unsubscribe failed"
        
        print("[OK] test_shared_state_callbacks PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_concurrent_access():
    """Test thread safety dengan concurrent readers/writers."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        errors = []
        results = []
        
        def writer():
            try:
                for i in range(100):
                    state.set("counter", i)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"writer: {e}")
        
        def reader():
            try:
                for _ in range(100):
                    val = state.get("counter", -1)
                    if val != -1:
                        results.append(val)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"reader: {e}")
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        
        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) > 0, "No successful reads"
        
        print("[OK] test_shared_state_concurrent_access PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_delete_and_clear():
    """Test delete and clear operations."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        
        state.set("a", 1)
        state.set("b", 2)
        state.set("c", 3)
        
        # Test delete
        assert state.delete("b") == True
        assert state.get("b") is None
        assert state.get("a") == 1
        assert state.get("c") == 3
        
        # Delete non-existent
        assert state.delete("nonexistent") == False
        
        # Test clear
        state.clear()
        assert state.get("a") is None
        assert state.get("c") is None
        assert state.get_all() == {}
        
        print("[OK] test_shared_state_delete_and_clear PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_get_all():
    """Test get_all returns complete state."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        state = SharedState(tmpfile)
        
        state.set("x", 10)
        state.set("y", 20)
        state.set("z", 30)
        
        all_data = state.get_all()
        assert all_data == {"x": 10, "y": 20, "z": 30}
        
        # Modifikasi return value tidak boleh affect internal state
        all_data["x"] = 999
        assert state.get("x") == 10
        
        print("[OK] test_shared_state_get_all PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def test_shared_state_persistence():
    """Test state persists across instances."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmpfile = f.name
    
    try:
        # Instance 1
        state1 = SharedState(tmpfile)
        state1.set("persisted", "value")
        state1.set("number", 123)
        
        # Instance 2 (simulate restart)
        state2 = SharedState(tmpfile)
        
        assert state2.get("persisted") == "value"
        assert state2.get("number") == 123
        
        print("[OK] test_shared_state_persistence PASSED")
        
    finally:
        if os.path.exists(tmpfile):
            os.unlink(tmpfile)


def run_all_tests():
    """Jalankan semua test."""
    print("Menjalankan test SharedState...\n")
    
    test_shared_state_basic()
    test_shared_state_update()
    test_shared_state_callbacks()
    test_shared_state_concurrent_access()
    test_shared_state_delete_and_clear()
    test_shared_state_get_all()
    test_shared_state_persistence()
    
    print("\n[SUCCESS] SEMUA TEST BERHASIL!")


if __name__ == "__main__":
    run_all_tests()