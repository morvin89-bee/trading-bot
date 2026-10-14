"""Migrasi sekali: pindahkan pembelian dari dca.db ke portfolio.db.

Idempoten — baris dengan `external_id` yang sama tidak diduplikasi, jadi
aman dijalankan berulang kali.
"""
import os
import sqlite3

import portfolio_store as ps


def migrate(dca_path, conn):
    """Pindahkan tabel `purchases` dari dca.db -> `trades` di portfolio.db.

    Mengembalikan jumlah baris baru yang dimasukkan. Bila file atau tabel
    tidak ada, kembalikan 0 (tanpa error).
    """
    if not os.path.exists(dca_path):
        return 0

    src = sqlite3.connect(dca_path)
    try:
        rows = src.execute(
            "SELECT id, pair, idr, price, coins, bought_at, api_trade_id "
            "FROM purchases").fetchall()
    except sqlite3.OperationalError:
        return 0
    finally:
        src.close()

    inserted = 0
    for rid, pair, idr, price, coins, bought_at, api_id in rows:
        trade = {
            "pair": pair,
            "side": "buy",
            "price": price,
            "qty": coins,
            "quote": idr,
            "fee": 0,
            "ts": bought_at,
            "source": "dca_db",
            "external_id": api_id or f"dca:{rid}",
        }
        if ps.insert_trade(conn, trade):
            inserted += 1
    return inserted
