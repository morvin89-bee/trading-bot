"""
Pemantau transaksi (metode baru).
Mengumpulkan data transaksi (trades) publik dari Indodax ke SQLite (trades.db).
Data ini dipakai analyzer.py untuk belajar & prediksi statistik.

Catatan: endpoint /api/trades/{pair} hanya mengembalikan transaksi TERBARU
(500 terakhir), jadi data harus dikumpulkan dari sekarang (tidak bisa mundur
ke masa lalu). Semakin lama berjalan, semakin bagus analisisnya.
Endpoint ini pakai format pair LAMA (btcidr), bukan btc_idr.
"""

import sqlite3
import time
from datetime import datetime

import scanner

PAIRS = [pair_id for _, pair_id in scanner.PAIRS]
INTERVAL = 10
DB_FILE = "trades.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            pair TEXT,
            tid INTEGER,
            date INTEGER,
            price REAL,
            amount REAL,
            type TEXT,
            fetched_at TEXT,
            PRIMARY KEY (pair, tid)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS depth_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            date INTEGER,
            bid_vol REAL,
            ask_vol REAL,
            imbalance REAL,
            fetched_at TEXT
        )"""
    )
    conn.commit()
    return conn


def fetch_trades(conn, pair_id):
    """Ambil trades terbaru, simpan yang belum ada. Return jumlah baru.

    Catatan: endpoint trades pakai format pair LAMA (tanpa underscore),
    mis. btcidr — berbeda dengan endpoint ticker (btc_idr).
    """
    api_pair = pair_id.replace("_", "")
    data = scanner._get_json(f"https://indodax.com/api/trades/{api_pair}")
    new = 0
    now = datetime.now().isoformat(timespec="seconds")
    for t in data:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO trades "
                "(pair, tid, date, price, amount, type, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    pair_id,
                    int(t["tid"]),
                    int(t["date"]),
                    float(t["price"]),
                    float(t["amount"]),
                    t["type"],
                    now,
                ),
            )
            new += cur.rowcount
        except (KeyError, ValueError, TypeError):
            continue
    conn.commit()
    return new


def fetch_depth(conn, pair_id, levels=10):
    """Ambil snapshot order book, hitung order book imbalance.

    Imbalance = (vol bid - vol ask) / (vol bid + vol ask)
    Rentang -1 (dominan jual) .. +1 (dominan beli).
    Endpoint depth pakai format pair LAMA (btcidr).
    """
    import time as _time

    api_pair = pair_id.replace("_", "")
    data = scanner._get_json(f"https://indodax.com/api/depth/{api_pair}")
    bids = data.get("buy", [])[:levels]
    asks = data.get("sell", [])[:levels]
    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO depth_snapshots "
        "(pair, date, bid_vol, ask_vol, imbalance, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pair_id, int(_time.time()), bid_vol, ask_vol, imbalance, now),
    )
    conn.commit()
    return imbalance


def main():
    conn = init_db()
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    start = datetime.now()
    print("Pemantau transaksi Indodax (Ctrl+C untuk berhenti)")
    print(f"Pair: {len(PAIRS)} | Database: {DB_FILE} ({total} transaksi tersimpan)")
    print(f"Mulai: {start:%d %b %Y %H:%M}\n")

    crash = 0
    cycle = 0
    while True:
        try:
            new_total = 0
            for pair_id in PAIRS:
                try:
                    new_total += fetch_trades(conn, pair_id)
                except Exception as e:
                    print(f"  {pair_id}: gagal -> {str(e)[:70]}")
            # snapshot order book tiap 2 siklus (jaga rate limit)
            cycle += 1
            if cycle % 2 == 0:
                for pair_id in PAIRS:
                    try:
                        fetch_depth(conn, pair_id)
                    except Exception as e:
                        print(f"  depth {pair_id}: gagal -> {str(e)[:70]}")
            total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            print(f"[{datetime.now():%H:%M:%S}] +{new_total} baru | total {total}")
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            print(f"\nBerhenti. Total transaksi tersimpan: {total}")
            raise
        except Exception as e:
            # auto-pulih: sambung ulang database, lanjutkan
            crash += 1
            print(f"  [!] error tak terduga (#{crash}): {str(e)[:90]}")
            try:
                conn.close()
            except Exception:
                pass
            conn = init_db()
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
