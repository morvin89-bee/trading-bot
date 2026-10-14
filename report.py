"""
Analisis harian paper trading.
Membaca signals.csv, menampilkan ringkasan di terminal,
dan membuat laporan HTML dengan grafik (SVG, tanpa koneksi internet).

Pakai: python report.py [--demo]
"""

import csv
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta

LOG_FILE = "signals.csv"
DEMO_FILE = "demo_signals.csv"
REPORT_FILE = "laporan.html"

CSS = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; background: #f5f5f5; }
h1 { font-size: 22px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }
.card { background: #fff; border-radius: 8px; padding: 14px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.12); min-width: 140px; }
.card .label { font-size: 12px; color: #666; }
.card .value { font-size: 20px; font-weight: bold; }
.pos { color: #2e7d32; } .neg { color: #c62828; }
.chart { background: #fff; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.12); margin: 12px 0; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid #ddd; font-size: 13px; }
th { background: #eee; text-align: center; }
td.l, th.l { text-align: left; }
.note { font-size: 12px; color: #666; margin: 2px 0 10px; }
.card .note { margin: 6px 0 0; }
.legend { font-size: 12px; color: #666; margin: 8px 0 0; }
.banner { padding: 10px 16px; border-radius: 8px; font-weight: bold; margin: 10px 0 16px; font-size: 14px; }
.banner.demo { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
.banner.real { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
h2 { font-size: 17px; margin: 20px 0 4px; }
h3 { font-size: 14px; margin: 4px 0; }
"""


def load_signals(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def analyze(rows):
    stats = {
        "total": len(rows),
        "wins": 0,
        "pnl": 0.0,
        "by_pair": defaultdict(lambda: {"count": 0, "pnl": 0.0}),
        "by_direction": defaultdict(lambda: {"count": 0, "pnl": 0.0}),
        "by_day": defaultdict(float),
        "best": None,
        "worst": None,
        "equity": [],
    }
    cum = 0.0
    for r in rows:
        pnl = float(r["pnl_idr"])
        stats["pnl"] += pnl
        if pnl > 0:
            stats["wins"] += 1
        stats["by_day"][r["time"][:10]] += pnl
        stats["by_pair"][r["pair"]]["count"] += 1
        stats["by_pair"][r["pair"]]["pnl"] += pnl
        direction = f"{r['buy_exchange']} -> {r['sell_exchange']}"
        stats["by_direction"][direction]["count"] += 1
        stats["by_direction"][direction]["pnl"] += pnl
        cum += pnl
        stats["equity"].append((r["time"], cum))
        if stats["best"] is None or pnl > float(stats["best"]["pnl_idr"]):
            stats["best"] = r
        if stats["worst"] is None or pnl < float(stats["worst"]["pnl_idr"]):
            stats["worst"] = r
    return stats


def _yscale(vals, height, pad):
    lo = min(vals + [0.0])
    hi = max(vals + [0.0])
    if hi == lo:
        hi = lo + 1.0
    rng = hi - lo

    def y(v):
        return pad + (height - 2 * pad) * (1 - (v - lo) / rng)

    return y, lo, hi


def equity_chart(equity, width=760, height=280):
    if not equity:
        return "<p>Belum ada data perdagangan.</p>"
    pad = 40
    vals = [v for _, v in equity]
    y, lo, hi = _yscale(vals, height, pad)
    n = len(equity)

    def x(i):
        return pad + (width - 2 * pad) * (i / (n - 1)) if n > 1 else width / 2

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(equity))
    zero_y = y(0)
    return f"""
<svg width="{width}" height="{height}" role="img">
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}"
        stroke="#999" stroke-dasharray="4"/>
  <polygon points="{pad},{height - pad} {pts} {width - pad},{height - pad}"
           fill="#2e7d32" opacity="0.12"/>
  <polyline points="{pts}" fill="none" stroke="#2e7d32" stroke-width="2"/>
  <text x="{pad}" y="{height - 12}" font-size="11" fill="#555">{equity[0][0][:16]}</text>
  <text x="{width - pad}" y="{height - 12}" font-size="11" fill="#555" text-anchor="end">{equity[-1][0][:16]}</text>
  <text x="{pad - 4}" y="{y(hi) + 4:.1f}" font-size="10" fill="#555" text-anchor="end">{hi:,.0f}</text>
  <text x="{pad - 4}" y="{y(lo) + 4:.1f}" font-size="10" fill="#555" text-anchor="end">{lo:,.0f}</text>
  <text x="{width - pad}" y="{y(hi) + 12:.1f}" font-size="10" fill="#555" text-anchor="end">P&amp;L kumulatif (IDR)</text>
</svg>"""


def bar_chart(data, title, width=360, height=240):
    if not data:
        return ""
    pad = 35
    items = list(data.items())
    vals = [v for v in items.values()] if False else [v for _, v in items]
    y, lo, hi = _yscale(vals, height, pad)
    n = len(items)
    gap = (width - 2 * pad) / n
    bw = gap * 0.6
    parts = [f'<text x="{width / 2}" y="16" font-size="13" text-anchor="middle" font-weight="bold">{title}</text>']
    for i, (label, v) in enumerate(items):
        bx = pad + gap * i + (gap - bw) / 2
        by = y(max(v, 0))
        bh = max(abs(y(v) - y(0)), 1)
        color = "#2e7d32" if v >= 0 else "#c62828"
        parts.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{bx + bw / 2:.1f}" y="{height - pad + 14}" font-size="10" text-anchor="middle">{label}</text>'
        )
        val_y = by - 4 if v >= 0 else by + bh + 11
        parts.append(
            f'<text x="{bx + bw / 2:.1f}" y="{val_y:.1f}" font-size="9" text-anchor="middle" fill="#333">{v:,.0f}</text>'
        )
    zero_y = y(0)
    parts.insert(
        1,
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" stroke="#999" stroke-dasharray="4"/>',
    )
    return f'<svg width="{width}" height="{height}">' + "".join(parts) + "</svg>"


def build_html(stats, demo=False):
    winrate = stats["wins"] / stats["total"] * 100 if stats["total"] else 0
    pnl_class = "pos" if stats["pnl"] >= 0 else "neg"
    pair_rows = "".join(
        f"<tr><td class='l'>{p.upper()}</td><td>{d['count']}</td>"
        f"<td class='{'pos' if d['pnl'] >= 0 else 'neg'}'>{d['pnl']:,.0f}</td></tr>"
        for p, d in sorted(stats["by_pair"].items())
    )
    dir_rows = "".join(
        f"<tr><td class='l'>{d}</td><td>{v['count']}</td>"
        f"<td class='{'pos' if v['pnl'] >= 0 else 'neg'}'>{v['pnl']:,.0f}</td></tr>"
        for d, v in sorted(stats["by_direction"].items())
    )
    day_rows = "".join(
        f"<tr><td class='l'>{day}</td>"
        f"<td class='{'pos' if pnl >= 0 else 'neg'}'>{pnl:,.0f}</td></tr>"
        for day, pnl in sorted(stats["by_day"].items())
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Laporan Paper Trading</title>
<style>{CSS}</style></head><body>
{"<div class='banner demo'>DATA CONTOH (demo_signals.csv) — angka-angka di halaman ini hasil generator acak, BUKAN hasil paper trading asli Anda.</div>" if demo else "<div class='banner real'>DATA ASLI (signals.csv) — hasil paper trading Anda sendiri.</div>"}
<h1>Laporan Paper Trading Arbitrase</h1>
<p class="note">Laporan ini dibuat otomatis dari data <b>paper trading</b> (simulasi tanpa uang asli).
Semua angka bersifat <b>virtual</b> dan hanya untuk latihan &mdash; bukan keuntungan nyata.</p>
<p class="note"><b>Metode simulasi:</b> setiap trade memakai nominal <b>tetap (statis)</b> Rp 1.000.000 &mdash;
tidak tergantung besar kecilnya spread maupun saldo exchange.
P&amp;L per trade = nominal &times; selisih harga bersih (setelah fee &amp; slippage) &minus; biaya transfer.</p>

<h2>Ringkasan</h2>
<p class="note">Statistik keseluruhan dari semua trade yang disimulasikan bot.</p>
<div class="cards">
  <div class="card"><div class="label">Total Trade</div><div class="value">{stats['total']}</div>
    <div class="note">Jumlah simulasi trade yang dieksekusi bot</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value">{winrate:.1f}%</div>
    <div class="note">Persentase trade yang menghasilkan profit</div></div>
  <div class="card"><div class="label">Total P&amp;L</div><div class="value {pnl_class}">{stats['pnl']:,.0f} IDR</div>
    <div class="note">Total keuntungan (+) / kerugian (&minus;) virtual dalam Rupiah</div></div>
  <div class="card"><div class="label">Trade Terbaik</div><div class="value pos">{(float(stats['best']['pnl_idr']) if stats['best'] else 0):,.0f} IDR</div>
    <div class="note">Trade dengan keuntungan terbesar</div></div>
  <div class="card"><div class="label">Trade Terburuk</div><div class="value neg">{(float(stats['worst']['pnl_idr']) if stats['worst'] else 0):,.0f} IDR</div>
    <div class="note">Trade dengan kerugian terbesar</div></div>
</div>
<p class="legend">Cara baca warna: <span class="pos"><b>hijau</b></span> = untung, <span class="neg"><b>merah</b></span> = rugi.</p>

<div class="chart">
  <h3>Kurva P&amp;L Kumulatif</h3>
  <p class="note">Grafik perubahan total keuntungan dari waktu ke waktu. Garis di atas nol = secara keseluruhan untung; di bawah nol = rugi.</p>
  {equity_chart(stats['equity'])}
</div>
<div style="display:flex;gap:12px;flex-wrap:wrap">
  <div class="chart">{bar_chart({p: d['pnl'] for p, d in stats['by_pair'].items()}, 'P&amp;L per Pair (IDR)')}
    <p class="note">Total profit/rugi untuk setiap pasangan (BTC, ETH, dst). Pair dengan P&amp;L tertinggi paling sering memberi peluang.</p>
  </div>
  <div class="chart">{bar_chart({d: v['pnl'] for d, v in stats['by_direction'].items()}, 'P&amp;L per Arah (IDR)')}
    <p class="note">Profit/rugi berdasarkan arah arbitrase. Bandingkan apakah lebih sering untung saat beli di Indodax atau di Tokocrypto.</p>
  </div>
  <div class="chart">{bar_chart(dict(stats['by_day']), 'P&amp;L Harian (IDR)')}
    <p class="note">Total profit/rugi per hari. Berguna untuk melihat tren performa bot dari hari ke hari.</p>
  </div>
</div>

<h2>Detail per Pair</h2>
<p class="note">Jumlah trade dan total P&amp;L untuk setiap pasangan mata uang.</p>
<table><tr><th class="l">Pair</th><th>Trade</th><th>P&amp;L (IDR)</th></tr>{pair_rows}</table>
<h2>Detail per Arah</h2>
<p class="note">Jumlah trade dan total P&amp;L untuk setiap arah arbitrase.</p>
<table><tr><th class="l">Arah</th><th>Trade</th><th>P&amp;L (IDR)</th></tr>{dir_rows}</table>
<h2>P&amp;L Harian</h2>
<p class="note">Total P&amp;L per hari kalender.</p>
<table><tr><th class="l">Tanggal</th><th>P&amp;L (IDR)</th></tr>{day_rows}</table>
</body></html>"""


def print_summary(stats):
    print("=== Ringkasan Paper Trading ===")
    print(f"Total trade : {stats['total']}")
    if stats["total"]:
        print(f"Win rate    : {stats['wins'] / stats['total'] * 100:.1f}%")
        print(f"Total P&L   : {stats['pnl']:,.0f} IDR")
        print(f"Terbaik     : {float(stats['best']['pnl_idr']):,.0f} IDR ({stats['best']['pair']} @ {stats['best']['time']})")
        print(f"Terburuk    : {float(stats['worst']['pnl_idr']):,.0f} IDR ({stats['worst']['pair']} @ {stats['worst']['time']})")
        print("P&L harian  :")
        for day, pnl in sorted(stats["by_day"].items()):
            print(f"  {day}: {pnl:,.0f} IDR")
    else:
        print("Belum ada sinyal. Jalankan paper_trader.py dulu.")


def make_demo(path):
    random.seed(42)
    rows = []
    pairs = ["btc_idr", "eth_idr", "usdt_idr", "bnb_idr"]
    dirs = [("Indodax", "Tokocrypto"), ("Tokocrypto", "Indodax")]
    base = datetime(2026, 10, 6, 9, 0, 0)
    for i in range(72):
        t = base + timedelta(minutes=i * 41)
        pair = random.choice(pairs)
        b, s = random.choice(dirs)
        net = random.uniform(-0.001, 0.012)
        pnl = 1_000_000 * (net - 0.001)
        rows.append(
            {
                "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "pair": pair,
                "buy_exchange": b,
                "buy_price": "0",
                "sell_exchange": s,
                "sell_price": "0",
                "net_after_fee_pct": f"{net * 100:.3f}",
                "slippage_pct": "0.100",
                "pnl_idr": f"{pnl:.0f}",
                "transfer_fee_idr": "0",
                "balance_indodax": "0",
                "balance_tokocrypto": "0",
            }
        )
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    demo = "--demo" in sys.argv
    path = DEMO_FILE if demo else LOG_FILE

    if demo:
        rows = make_demo(path)
        print(f"Data demo dibuat: {path} ({len(rows)} sinyal sintetis)")
    else:
        rows = load_signals(path)
        if not rows:
            print(f"{path} belum ada / masih kosong.")
            print("Jalankan 'python paper_trader.py' dulu, atau 'python report.py --demo' untuk lihat contoh laporan.")
            return

    stats = analyze(rows)
    print_summary(stats)

    html = build_html(stats, demo=demo)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Laporan HTML: {os.path.abspath(REPORT_FILE)}")
    try:
        os.startfile(REPORT_FILE)
    except Exception:
        print("Buka file di atas manually di browser.")


if __name__ == "__main__":
    main()
