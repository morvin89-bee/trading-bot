"""DCA Tracker - catat pembelian DCA, pantau rata-rata biaya dan profit.

Penggunaan:
  python dca_tracker.py add                          # input interaktif
  python dca_tracker.py add btc 1000000 150000000    # pair, total IDR, harga/koin
  python dca_tracker.py add btc 1000000 150000000 2026-10-10
  python dca_tracker.py list
  python dca_tracker.py status
  python dca_tracker.py check             # verifikasi API key
  python dca_tracker.py sync              # tarik otomatis dari Indodax (auto-detect API v1/v2)
  python dca_tracker.py remove <id>

Penyimpanan: dca.db (SQLite, tabel purchases).
Hapus file dca.db untuk memulai dari awal.

Catatan: Indodax Recurring Investment hanya mendukung BTC, ETH, USDT.
Harga kini diambil otomatis dari API publik Indodax.
"""
import os
import sqlite3
import sys
import time

import price_feed

DB_FILE = "dca.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            idr REAL NOT NULL,
            price REAL NOT NULL,
            coins REAL NOT NULL,
            bought_at INTEGER NOT NULL,
            api_trade_id TEXT UNIQUE
        )
    """)
    try:
        conn.execute("ALTER TABLE purchases ADD COLUMN api_trade_id TEXT UNIQUE")
    except sqlite3.OperationalError:
        pass  # kolom sudah ada
    return conn


def normalize_pair(raw):
    p = raw.strip().lower().replace("-", "_")
    if not p.endswith("_idr"):
        p += "_idr"
    return p


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        t = time.localtime()
    else:
        try:
            t = time.strptime(raw, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"format tanggal salah: {raw} (pakai YYYY-MM-DD)")
    return int(time.mktime(t))


def fmt_idr(x):
    return "Rp " + f"{x:,.0f}".replace(",", ".")


def fmt_num(x, dec=0):
    return f"{x:,.{dec}f}".replace(",", ".")


def cmd_add(args):
    if len(args) >= 3:
        pair = normalize_pair(args[0])
        idr = float(args[1])
        price = float(args[2])
        bought_at = parse_date(args[3] if len(args) > 3 else "")
    else:
        pair = normalize_pair(input("Pair (mis. btc): "))
        idr = float(input("Total IDR yang dikeluarkan: "))
        price = float(input("Harga beli per koin (Rp): "))
        bought_at = parse_date(input("Tanggal (YYYY-MM-DD, kosong = hari ini): "))
    if idr <= 0 or price <= 0:
        print("IDR dan harga harus lebih dari 0")
        return
    coins = idr / price
    conn = init_db()
    conn.execute(
        "INSERT INTO purchases (pair,idr,price,coins,bought_at) VALUES (?,?,?,?,?)",
        (pair, idr, price, coins, bought_at))
    conn.commit()
    conn.close()
    tanggal = time.strftime("%Y-%m-%d", time.localtime(bought_at))
    print(f"Tercatat [{tanggal}] {pair}: {fmt_idr(idr)} @ "
          f"{fmt_idr(price)} = {coins:.8f} koin")


def cmd_list():
    conn = init_db()
    rows = conn.execute(
        "SELECT id, pair, idr, price, coins, bought_at FROM purchases "
        "ORDER BY bought_at, id").fetchall()
    conn.close()
    if not rows:
        print("Belum ada pembelian tercatat.")
        return
    print(f"{'ID':>4} {'Tanggal':<12}{'Pair':<10}{'Total IDR':>18}"
          f"{'Harga/koin':>18}{'Koin':>16}")
    print("-" * 84)
    for pid, pair, idr, price, coins, bought_at in rows:
        tanggal = time.strftime("%Y-%m-%d", time.localtime(bought_at))
        print(f"{pid:>4} {tanggal:<12}{pair:<10}{fmt_idr(idr):>18}"
              f"{fmt_idr(price):>18}{coins:>16.8f}")


def cmd_status():
    conn = init_db()
    rows = conn.execute(
        "SELECT pair, COUNT(*), SUM(idr), SUM(coins) FROM purchases "
        "GROUP BY pair ORDER BY pair").fetchall()
    conn.close()
    if not rows:
        print("Belum ada pembelian tercatat. Gunakan: python dca_tracker.py add")
        return
    print(f"{'Pair':<10}{'Beli':>5}{'Modal':>18}{'Rata-rata':>18}"
          f"{'Harga kini':>18}{'Nilai kini':>18}{'Profit':>10}")
    print("-" * 97)
    tot_modal = tot_nilai = 0.0
    for pair, n, modal, coins in rows:
        avg = modal / coins
        try:
            bid, ask = price_feed.indodax_price(pair)
            kini = (bid + ask) / 2
        except Exception:
            kini = None
        if kini:
            nilai = coins * kini
            pnl = (nilai - modal) / modal * 100
            tot_modal += modal
            tot_nilai += nilai
            print(f"{pair:<10}{n:>5}{fmt_idr(modal):>18}{fmt_idr(avg):>18}"
                  f"{fmt_idr(kini):>18}{fmt_idr(nilai):>18}{pnl:>+9.1f}%")
        else:
            print(f"{pair:<10}{n:>5}{fmt_idr(modal):>18}{fmt_idr(avg):>18}"
                  f"{'n/a':>18}{'n/a':>18}{'n/a':>10}")
    print("-" * 97)
    if tot_modal > 0:
        pnl = (tot_nilai - tot_modal) / tot_modal * 100
        print(f"{'TOTAL':<10}{'':>5}{fmt_idr(tot_modal):>18}{'':>18}"
              f"{'':>18}{fmt_idr(tot_nilai):>18}{pnl:>+9.1f}%")
    print("\nIni bukan saran investasi. DCA bekerja karena konsistensi, bukan timing.")


DCA_PAIRS = ["btc_idr", "eth_idr", "usdt_idr"]


def cmd_sync():
    """Tarik riwayat pembelian dari Indodax API (auto-detect TAPI v1/v2)."""
    import indodax_api
    total_baru = 0
    for pair in DCA_PAIRS:
        try:
            trades = indodax_api.my_trades(pair)
        except Exception as e:
            print(f"{pair}: gagal -> {str(e)[:100]}")
            continue
        if not trades:
            print(f"{pair}: tidak ada transaksi di API")
            continue
        conn = init_db()
        baru = dilewatkan = 0
        for t in trades:
            if t["side"] != "buy" or not t["id"]:
                dilewatkan += 1
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO purchases "
                "(pair,idr,price,coins,bought_at,api_trade_id) "
                "VALUES (?,?,?,?,?,?)",
                (pair, t["quote"], t["price"], t["qty"],
                 t["time"], t["id"]))
            if cur.rowcount:
                baru += 1
        conn.commit()
        conn.close()
        total_baru += baru
        print(f"{pair}: {len(trades)} transaksi, {baru} pembelian baru"
              + (f", {dilewatkan} dilewati" if dilewatkan else ""))
    print(f"Total pembelian baru: {total_baru}")
    if total_baru == 0:
        print("Catatan: kalau transaksi Recurring Investment tidak muncul "
              "di API, catat manual dengan perintah 'add'.")


def cmd_check():
    """Verifikasi API key: koneksi, izin, versi API terdeteksi."""
    import indodax_api
    try:
        info = indodax_api.account_info()
    except Exception as e:
        print("Gagal:", str(e)[:150])
        return
    versi = indodax_api._WORKING or "?"
    print(f"Koneksi berhasil (TAPI {versi})")
    if isinstance(info, dict) and "canTrade" in info:
        print(f"  canTrade: {info.get('canTrade')}")
        print(f"  canWithdraw: {info.get('canWithdraw')}")
        print(f"  accountType: {info.get('accountType')}")
        n = len(info.get("balances") or [])
        print(f"  aset dengan saldo: {n}")
        if info.get("canWithdraw"):
            print("  PERINGATAN: key ini punya izin withdraw!")
    elif isinstance(info, dict) and info.get("success"):
        r = info.get("return") or {}
        print(f"  server_time: {r.get('server_time')}")
        print(f"  saldo BTC: {r.get('balance')}")
        print(f"  saldo IDR: {r.get('balance_rp')}")
    else:
        print("  Respons:", str(info)[:200])


def cmd_remove(args):
    if not args:
        print("Pakai: python dca_tracker.py remove <id>  (lihat ID di 'list')")
        return
    conn = init_db()
    row = conn.execute("SELECT id, pair, idr, bought_at FROM purchases "
                       "WHERE id = ?", (int(args[0]),)).fetchone()
    if not row:
        print(f"Tidak ada pembelian dengan ID {args[0]}")
        conn.close()
        return
    pid, pair, idr, bought_at = row
    tanggal = time.strftime("%Y-%m-%d", time.localtime(bought_at))
    print(f"Hapus [{pid}] {tanggal} {pair} {fmt_idr(idr)}? (ya/tidak)")
    jawab = input().strip().lower()
    if jawab == "ya":
        conn.execute("DELETE FROM purchases WHERE id = ?", (pid,))
        conn.commit()
        print("Dihapus.")
    else:
        print("Batal.")
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "add":
        cmd_add(args)
    elif cmd == "list":
        cmd_list()
    elif cmd == "status":
        cmd_status()
    elif cmd == "check":
        cmd_check()
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "remove":
        cmd_remove(args)
    else:
        print("Perintah tidak dikenal:", cmd)
        print("Pilihan: add | list | status | check | sync | remove")


if __name__ == "__main__":
    main()
