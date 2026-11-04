"""Facade entrypoint untuk `main_loop.main`."""
import os
import sys

if __package__ in (None, ""):
    # Jalankan sebagai skrip: pastikan root proyek ada di depan `core/`
    # supaya `main_loop` yang diimpor adalah modul produksi, bukan file ini.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
