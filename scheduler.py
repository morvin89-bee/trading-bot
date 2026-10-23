"""Snapshot harian portofolio — untuk dijalankan Windows Task Scheduler.

Read-only: hanya membaca bursa (saldo & riwayat) dan menulis satu baris
snapshot ke `portfolio.db`. Tidak pernah mengirim order.

Contoh:
    python scheduler.py
    python scheduler.py --telegram
"""
import argparse
import os

import alerts
import indodax_api
import portfolio
import portfolio_store


def _env(key):
    """Ambil dari environment atau .env (kunci tidak perlu hardcode)."""
    return os.getenv(key) or indodax_api._load_env().get(key, "")


def _rp(x):
    try:
        return "Rp " + f"{float(x):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"


def take_snapshot(conn):
    """Ambil data terbaru dan tulis satu snapshot.

    `portfolio.refresh` yang menulis baris snapshot; fungsi ini hanya
    jembatan tipis supaya mudah dijadwalkan dan diuji.
    """
    return portfolio.refresh(conn)


def _summary(out):
    """Teks ringkasan snapshot (dikirim ke Telegram bila `--telegram`).

    IDR diturunkan dari total - nilai aset, sama seperti tab Portofolio:
    `portfolio.compute_holdings` sengaja melewati IDR, jadi tanpa baris ini
    komposisi uang tunai tidak terlihat di Telegram.
    """
    holdings = out.get("holdings", [])
    total = float(out.get("total_idr", 0) or 0)
    idr = total - sum(float(h.value_idr or 0) for h in holdings)
    lines = [f"Snapshot portofolio: {_rp(total)}"]
    if idr:
        lines.append(f"  {'IDR':<4} {_rp(idr)}")
    for h in holdings:
        lines.append(f"  {h.coin.upper():<4} {_rp(h.value_idr)}")
    for m in alerts.build_alerts(holdings, out.get("actions", [])):
        lines.append(f"  * {m}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scheduler",
        description="Snapshot harian portofolio Indodax (read-only).")
    parser.add_argument("--telegram", action="store_true",
                        help="kirim ringkasan ke Telegram bila token diset")
    args = parser.parse_args(argv)

    conn = portfolio_store.connect(portfolio_store.DB_FILE)
    portfolio_store.init_db(conn)
    try:
        try:
            out = take_snapshot(conn)
        except Exception as e:
            print(f"Gagal mengambil snapshot: {e}")
            return 1
        text = _summary(out)
        print(text)

        if args.telegram:
            token = _env("TELEGRAM_TOKEN")
            chat = _env("TELEGRAM_CHAT_ID")
            if token and chat:
                ok = alerts.send_telegram(token, chat, text)
                print("Telegram terkirim." if ok else "Telegram gagal.")
            else:
                print("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID belum diset; dilewati.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
