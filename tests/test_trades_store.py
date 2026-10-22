"""Tes trades_store: penyimpanan pemantauan per HARI (satu file per tanggal).

Tanpa jaringan. Semua file dibuat di direktori sementara lewat env
`TRADES_DB_DIR` — repo tidak tersentuh.
"""

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trades_store


def _epoch(tanggal):
    return int(time.mktime(time.strptime(tanggal, "%Y-%m-%d")))


def _isi_depth(tanggal, n, pair="btc_idr", mulai=0):
    """Tulis n baris depth_snapshots pada satu hari."""
    conn = trades_store.koneksi_hari(tanggal, tulis=True)
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO depth_snapshots (pair, date, bid_vol, ask_vol, imbalance, fetched_at) "
                "VALUES (?, ?, 1000, 1000, ?, ?)",
                (pair, _epoch(tanggal) + mulai + i, 0.5, tanggal))
        conn.commit()
    finally:
        conn.close()


def test_file_per_hari_dan_daftar(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    _isi_depth("2026-10-21", 2)
    _isi_depth("2026-10-22", 3)

    assert trades_store.daftar_hari() == ["2026-10-21", "2026-10-22"]
    assert os.path.exists(trades_store.path_hari("2026-10-21"))
    assert not os.path.exists(trades_store.path_hari("2026-10-23"))


def test_baca_file_yang_belum_ada(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        trades_store.koneksi_hari("2099-01-01")


def test_terakhir_lintas_hari(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    _isi_depth("2026-10-21", 3)          # 3 baris lama
    _isi_depth("2026-10-22", 2, mulai=100)  # 2 baris baru

    # 4 terbaru: 2 dari hari terbaru + 2 terbaru dari hari sebelumnya
    rows = trades_store.terakhir("depth_snapshots", "pair, date, imbalance",
                                 "btc_idr", limit=4)
    assert len(rows) == 4
    tanggal = [r[1] for r in rows]
    assert tanggal == sorted(tanggal, reverse=True)   # terbaru dulu
    assert all(r[0] == "btc_idr" for r in rows)


def test_gabung_query_lintas_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    _isi_depth("2026-10-21", 3)
    _isi_depth("2026-10-22", 2, mulai=100)

    conn = trades_store.gabung()
    try:
        assert conn.execute("SELECT COUNT(*) FROM depth_snapshots").fetchone()[0] == 5
        # query lama yang menulis nama tabel tanpa skema tetap jalan
        assert conn.execute(
            "SELECT COUNT(*) FROM depth_snapshots WHERE pair = ?", ("btc_idr",)
        ).fetchone()[0] == 5
        # tabel trades: PK (pair, tid) menangkap dobel, jadi gabungan bersih
        src = trades_store.koneksi_hari("2026-10-21", tulis=True)
        try:
            for _ in range(2):
                src.execute("INSERT OR IGNORE INTO trades "
                            "(pair, tid, date, price, amount, type, fetched_at) "
                            "VALUES ('btc_idr', 7, 1, 100, 1, 'buy', 'x')")
            src.commit()
        finally:
            src.close()
        gabung_ulang = trades_store.gabung()
        try:
            assert gabung_ulang.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        finally:
            gabung_ulang.close()
    finally:
        conn.close()


def test_migrasi_arsip(tmp_path, monkeypatch):
    """Migrasi file lama: per hari, tanggal aneh tidak hilang, bisa diulang."""
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    src = sqlite3.connect(str(tmp_path / "trades.db"))
    src.execute(
        "CREATE TABLE trades (pair TEXT, tid INTEGER, date INTEGER,"
        " price REAL, amount REAL, type TEXT, fetched_at TEXT,"
        " PRIMARY KEY (pair, tid))")
    src.execute(
        "CREATE TABLE depth_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " pair TEXT, date INTEGER, bid_vol REAL, ask_vol REAL,"
        " imbalance REAL, fetched_at TEXT)")
    src.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", [
        ("btc_idr", 1, _epoch("2026-10-21") + 10, 100, 1, "buy", "x"),
        ("btc_idr", 2, _epoch("2026-10-21") + 20, 101, 1, "buy", "x"),
        ("eth_idr", 3, _epoch("2026-10-22") + 5, 10, 1, "sell", "x"),
        ("btc_idr", 4, 0, 1, 1, "buy", "x"),   # tanggal tak masuk akal -> tetap di arsip
    ])
    src.execute("INSERT INTO depth_snapshots "
                "(pair, date, bid_vol, ask_vol, imbalance, fetched_at) "
                "VALUES ('btc_idr', ?, 1000, 1000, 0.5, 'x')", (_epoch("2026-10-21"),))
    src.commit()
    src.close()

    trades_store.migrasi()

    # tanggal 0 (1970-01-01) tetap terpecah ke hari miliknya, bukan hilang
    assert trades_store.daftar_hari() == ["1970-01-01", "2026-10-21", "2026-10-22"]
    k0 = trades_store.koneksi_hari("1970-01-01")
    try:
        assert k0.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    finally:
        k0.close()
    k1 = trades_store.koneksi_hari("2026-10-21")
    try:
        assert k1.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2
        assert k1.execute("SELECT COUNT(*) FROM depth_snapshots").fetchone()[0] == 1
    finally:
        k1.close()
    k2 = trades_store.koneksi_hari("2026-10-22")
    try:
        assert k2.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    finally:
        k2.close()
    # baris tanggal 0 tidak dipindah tapi tetap ada di arsip
    arsip = sqlite3.connect(trades_store.path_arsip())
    try:
        assert arsip.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 4
    finally:
        arsip.close()
    # file sumber sudah diganti nama -> migrasi ulang otomatis batal
    assert not os.path.exists(str(tmp_path / "trades.db"))
    trades_store.migrasi()


def test_buka_satu_hari_dan_rentang(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADES_DB_DIR", str(tmp_path))
    _isi_depth("2026-10-21", 3)
    _isi_depth("2026-10-22", 2, mulai=100)

    # satu hari -> langsung file hari itu (bukan salinan memori)
    awal, akhir = trades_store.rentang("2026-10-21")
    assert (awal, akhir) == ("2026-10-21", "2026-10-21")
    conn = trades_store.buka(awal, akhir)
    try:
        assert conn.execute("SELECT COUNT(*) FROM depth_snapshots").fetchone()[0] == 3
        berkas = conn.execute("PRAGMA database_list").fetchone()[2]
        assert berkas.endswith("trades_2026-10-21.db")
    finally:
        conn.close()

    # rentang dua hari -> gabungan di memori
    conn = trades_store.buka("2026-10-21", "2026-10-22")
    try:
        assert conn.execute("SELECT COUNT(*) FROM depth_snapshots").fetchone()[0] == 5
    finally:
        conn.close()

    # tanpa rentang -> semua hari
    assert trades_store.rentang(None) == (None, None)
    conn = trades_store.buka()
    try:
        assert conn.execute("SELECT COUNT(*) FROM depth_snapshots").fetchone()[0] == 5
    finally:
        conn.close()
