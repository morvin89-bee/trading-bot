"""Penyimpanan portofolio (SQLite) untuk sistem pertumbuhan portofolio Indodax.

Tabel:
  trades    - riwayat transaksi (beli / jual / reward staking)
  deposits  - setoran IDR manual
  snapshots - rekaman nilai portofolio harian

Semua fungsi di sini murni lokal (tanpa jaringan) agar mudah diuji.
"""
import sqlite3

DB_FILE = "portfolio.db"


def connect(db_path=DB_FILE):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            side TEXT CHECK(side IN ('buy','sell','reward')),
            price REAL,
            qty REAL,
            quote REAL,
            fee REAL DEFAULT 0,
            ts INTEGER,
            source TEXT,
            external_id TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idr REAL,
            ts INTEGER,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            total_idr REAL,
            contributions_idr REAL,
            market_idr REAL,
            breakdown_json TEXT
        );
        """
    )
    conn.commit()


def insert_trade(conn, trade):
    cur = conn.execute(
        "INSERT OR IGNORE INTO trades "
        "(pair, side, price, qty, quote, fee, ts, source, external_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (trade.get("pair"), trade.get("side"), trade.get("price"),
         trade.get("qty"), trade.get("quote"), trade.get("fee", 0),
         trade.get("ts"), trade.get("source"), trade.get("external_id")))
    conn.commit()
    return cur.rowcount == 1


def insert_deposit(conn, idr, ts, note=""):
    cur = conn.execute(
        "INSERT INTO deposits (idr, ts, note) VALUES (?,?,?)", (idr, ts, note))
    conn.commit()
    return cur.lastrowid


def list_trades(conn, pair=None):
    if pair is None:
        rows = conn.execute("SELECT * FROM trades ORDER BY ts, id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades WHERE pair = ? ORDER BY ts, id",
            (pair,)).fetchall()
    return [dict(r) for r in rows]


def list_deposits(conn):
    rows = conn.execute("SELECT * FROM deposits ORDER BY ts, id").fetchall()
    return [dict(r) for r in rows]


def total_contributions(conn):
    row = conn.execute(
        "SELECT COALESCE(SUM(idr), 0) AS s FROM deposits").fetchone()
    return row["s"]


def insert_snapshot(conn, snap):
    cur = conn.execute(
        "INSERT INTO snapshots "
        "(ts, total_idr, contributions_idr, market_idr, breakdown_json) "
        "VALUES (?,?,?,?,?)",
        (snap.get("ts"), snap.get("total_idr"), snap.get("contributions_idr"),
         snap.get("market_idr"), snap.get("breakdown_json")))
    conn.commit()
    return cur.lastrowid


def list_snapshots(conn):
    rows = conn.execute("SELECT * FROM snapshots ORDER BY ts, id").fetchall()
    return [dict(r) for r in rows]
