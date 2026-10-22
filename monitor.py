"""
Pemantau transaksi (metode baru).
Mengumpulkan data transaksi (trades) publik dari Indodax ke SQLite.
Data ini dipakai analyzer.py untuk belajar & prediksi statistik.

Sejak 2026-10-22 datanya per HARI: satu file `trades_YYYY-MM-DD.db`
(lihat trades_store.py). File lama `trades.db` jadi arsip setelah migrasi.

Catatan: endpoint /api/trades/{pair} hanya mengembalikan transaksi TERBARU
(500 terakhir), jadi data harus dikumpulkan dari sekarang (tidak bisa mundur
ke masa lalu). Semakin lama berjalan, semakin bagus analisisnya.
Endpoint ini pakai format pair LAMA (btcidr), bukan btc_idr.
"""

import time
from datetime import datetime

import scanner
import trades_store

PAIRS = [pair_id for _, pair_id in scanner.PAIRS]
INTERVAL = 10


def init_db():
    """Koneksi tulis ke file HARI INI (trades_YYYY-MM-DD.db)."""
    return trades_store.koneksi_hari(tulis=True)


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
    hari = trades_store.hari_ini()
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    start = datetime.now()
    print("Pemantau transaksi Indodax (Ctrl+C untuk berhenti)")
    print(f"Pair: {len(PAIRS)} | Database: {trades_store.path_hari(hari)} ({total} transaksi hari ini)")
    print(f"Mulai: {start:%d %b %Y %H:%M}\n")

    crash = 0
    cycle = 0
    while True:
        try:
            # ganti file saat hari berpindah; data lama tetap di file harinya
            if trades_store.hari_ini() != hari:
                conn.close()
                hari = trades_store.hari_ini()
                conn = init_db()
                print(f"[{datetime.now():%H:%M:%S}] ganti hari -> {trades_store.path_hari(hari)}")
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
            print(f"[{datetime.now():%H:%M:%S}] +{new_total} baru | total hari ini {total}")
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
            hari = trades_store.hari_ini()
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
