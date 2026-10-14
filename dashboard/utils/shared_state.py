"""
Thread-Safe Shared State Manager untuk komunikasi antar thread.
Menggunakan file JSON dengan lock (RLock) untuk keamanan konkurensi.
"""

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class SharedState:
    """
    Manajemen state yang thread-safe menggunakan file JSON sebagai backend.
    Mendukung callbacks untuk notifikasi perubahan.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = threading.RLock()
        self._callbacks: List[Callable[[str, Any], None]] = []
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Buat file JSON kosong jika belum ada."""
        if not os.path.exists(self.filepath):
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Ambil nilai dari state.
        
        Args:
            key: Kunci state
            default: Nilai default jika kunci tidak ada
            
        Returns:
            Nilai state atau default
        """
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get(key, default)
            except (json.JSONDecodeError, FileNotFoundError):
                return default

    def set(self, key: str, value: Any) -> None:
        """
        Set nilai state dan notify callbacks.
        
        Args:
            key: Kunci state
            value: Nilai yang akan disimpan (harus JSON serializable)
        """
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                data = {}
            
            data[key] = value
            
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            
            # Notify callbacks
            for cb in self._callbacks:
                try:
                    cb(key, value)
                except Exception:
                    pass  # Silent fail callbacks

    def update(self, keys: Dict[str, Any]) -> None:
        """
        Update multiple keys sekaligus (atomic).
        
        Args:
            keys: Dictionary key-value untuk diupdate
        """
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                data = {}
            
            data.update(keys)
            
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            
            # Notify callbacks untuk setiap key
            for k, v in keys.items():
                for cb in self._callbacks:
                    try:
                        cb(k, v)
                    except Exception:
                        pass

    def get_all(self) -> Dict[str, Any]:
        """
        Ambil seluruh state sebagai dictionary.
        
        Returns:
            Copy of entire state dict
        """
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}

    def subscribe(self, callback: Callable[[str, Any], None]) -> None:
        """
        Daftarkan callback untuk dipanggil saat state berubah.
        
        Args:
            callback: Fungsi dengan signature (key: str, value: Any) -> None
        """
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[str, Any], None]) -> bool:
        """
        Hapus callback yang terdaftar.
        
        Args:
            callback: Fungsi yang akan dihapus
            
        Returns:
            True jika callback ditemukan dan dihapus
        """
        with self._lock:
            try:
                self._callbacks.remove(callback)
                return True
            except ValueError:
                return False

    def delete(self, key: str) -> bool:
        """
        Hapus key dari state.
        
        Args:
            key: Kunci yang akan dihapus
            
        Returns:
            True jika key ada dan dihapus
        """
        with self._lock:
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return False
            
            if key not in data:
                return False
            
            del data[key]
            
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            
            return True

    def clear(self) -> None:
        """Hapus seluruh state (reset ke empty dict)."""
        with self._lock:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump({}, f)


# Global instance untuk kemudahan akses
_default_shared_state: Optional[SharedState] = None


def get_shared_state(filepath: str = "dashboard_shared_state.json") -> SharedState:
    """
    Mendapatkan instance SharedState global (singleton pattern).
    
    Args:
        filepath: Path file JSON untuk storage
        
    Returns:
        SharedState instance
    """
    global _default_shared_state
    if _default_shared_state is None:
        _default_shared_state = SharedState(filepath)
    return _default_shared_state