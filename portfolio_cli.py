"""CLI untuk mencatat setoran dan pembelian manual ke `portfolio.db`.

Pembelian dilakukan manual di aplikasi Indodax; CLI ini hanya mencatatnya
supaya perhitungan harga rata-rata dan laporan pertumbuhan tetap akurat.
Modul ini **read-only** terhadap bursa — tidak pernah mengirim order.

Contoh:
    python portfolio_cli.py deposit 500000 2026-10-01 --note "setoran Okt"
    python portfolio_cli.py buy btc 275000 1600000000
    python portfolio_cli.py status
    python portfolio_cli.py snapshot
"""
import argparse
import time

import alerts
import portfolio
import portfolio_store as ps

PAIR_ALIASES = {"btc": "btc_idr", "eth": "eth_idr", "sol": "sol_idr",
                "bch": "bch_idr"}


def _norm_pair(pair):
    p = (pair or "").strip().lower()
    if p in PAIR_ALIASES:
        return PAIR_ALIASES[p]
    if p and not p.endswith("_idr"):
        return f"{p}_idr"
    return p


def _parse_date(s):
    """Tanggal `YYYY-MM-DD` -> epoch detik (waktu lokal); kosong -> sekarang."""
    if not s:
        return int(time.time())
    return int(time.mktime(time.strptime(s, "%Y-%m-%d")))


def cmd_deposit(conn, idr, ts, note=""):
    """Catat setoran IDR (uang baru). Mengembalikan id baris."""
    return ps.insert_deposit(conn, float(idr), int(ts), note)


def cmd_buy(conn, pair, idr, price, ts):
    """Catat pembelian manual. `qty = idr / price`. False bila duplikat."""
    pair = _norm_pair(pair)
    price = float(price)
    qty = float(idr) / price if price else 0.0
    trade = {
        "pair": pair,
        "side": "buy",
        "price": price,
        "qty": qty,
        "quote": float(idr),
        "fee": 0,
        "ts": int(ts),
        "source": "manual",
        "external_id": None,
    }
    return ps.insert_trade(conn, trade)


def cmd_status(conn):
    """Rangkum portofolio dari cache terakhir (tanpa jaringan)."""
    out = portfolio.refresh(conn, online=False)
    lines = [f"Total portofolio: Rp {out['total_idr']:,.0f}"]
    for h in out["holdings"]:
        avg = f"Rp {h.avg_cost:,.0f}" if h.basis_known else "tidak diketahui"
        lines.append(f"  {h.coin.upper():<4} {h.qty:.8f}  rata-rata {avg}"
                     f"  ->  Rp {h.value_idr:,.0f}")
    lines.append(f"Kontribusi (total setoran): "
                 f"Rp {ps.total_contributions(conn):,.0f}")
    for msg in alerts.build_alerts(out["holdings"], out["actions"]):
        lines.append(f"  * {msg}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="portfolio_cli",
        description="Catat setoran & pembelian manual portofolio Indodax.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("deposit", help="catat setoran IDR")
    d.add_argument("idr", type=float)
    d.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (opsional)")
    d.add_argument("--note", default="")

    b = sub.add_parser("buy", help="catat pembelian manual")
    b.add_argument("pair")
    b.add_argument("idr", type=float)
    b.add_argument("price", type=float)
    b.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (opsional)")

    sub.add_parser("status", help="ringkasan portofolio")
    sub.add_parser("snapshot", help="ambil data online & simpan snapshot")

    args = parser.parse_args(argv)
    conn = ps.connect()
    ps.init_db(conn)
    try:
        if args.cmd == "deposit":
            cmd_deposit(conn, args.idr, _parse_date(args.date), args.note)
            print("Setoran dicatat.")
        elif args.cmd == "buy":
            ok = cmd_buy(conn, args.pair, args.idr, args.price,
                         _parse_date(args.date))
            print("Pembelian dicatat." if ok
                  else "Sudah pernah dicatat (diabaikan).")
        elif args.cmd == "status":
            print(cmd_status(conn))
        elif args.cmd == "snapshot":
            out = portfolio.refresh(conn, online=True)
            print(f"Snapshot disimpan. Total: Rp {out['total_idr']:,.0f}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
