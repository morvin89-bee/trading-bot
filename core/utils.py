"""Core utilities extracted from test helpers."""
import os
import sqlite3
import time

_URUTAN_DB = [0]


def _ts(hour=12, day=10, minute=0):
    """Timestamp epoch dari WAKTU LOKAL."""
    kini = time.localtime()
    return time.mktime(
        time.struct_time((kini.tm_year, kini.tm_mon, day, hour, minute, 0, 0, 0, -1))
    )


def _fresh_db(tmp_path):
    """Koneksi SQLite baru dan kosong — tabel TIDAK dibuat di sini."""
    _URUTAN_DB[0] += 1
    conn = sqlite3.connect(os.path.join(str(tmp_path), f"test{_URUTAN_DB[0]}.db"))
    conn.row_factory = sqlite3.Row
    return conn


class _TelegramPerekam:
    """Telegram tiruan yang mencatat pesan dan konfirmasi yang dikirim."""

    def __init__(self, token="token-uji"):
        self.token = token
        self.lines = []
        self.konfirmasi = []

    def line(self, text):
        self.lines.append(text)
        return True

    def confirmation(self, p):
        self.konfirmasi.append(p)
        return 4242  # message_id salah satu; None berarti "gagal kirim"
