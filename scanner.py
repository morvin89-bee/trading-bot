"""
Arbitrase Scanner - Indodax vs Tokocrypto
Fase 1: memantau harga & mendeteksi peluang arbitrase (BELUM trading otomatis).
"""

import logging
import time

import requests

logging.basicConfig(
    filename="errors.log",
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _get_json(url, params=None, max_retries=3):
    """GET request dengan retry + exponential backoff (termasuk HTTP 429).

    Setiap kegagalan dicatat ke errors.log untuk pemantauan.
    """
    delay = 2.0
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                last_error = "HTTP 429 (rate limit)"
                logging.warning(
                    "HTTP 429 dari %s, retry %d/%d setelah %.0fs",
                    url, attempt + 1, max_retries, delay,
                )
                print(f"  [!] HTTP 429 (rate limit) dari {url}, tunggu {delay:.0f}s")
            else:
                r.raise_for_status()
                return r.json()
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            logging.warning(
                "Request gagal %s (percobaan %d/%d): %s",
                url, attempt + 1, max_retries, e,
            )
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(
        f"Gagal setelah {max_retries} percobaan: {url} ({last_error})"
    )

# Pasangan yang dipantau: (simbol Tokocrypto, pair_id Indodax)
# pair_id Indodax formatnya pakai underscore, mis. btc_idr (lihat /api/pairs)
PAIRS = [
    ("BTCIDR", "btc_idr"),
    ("ETHIDR", "eth_idr"),
    ("USDTIDR", "usdt_idr"),
    ("BNBIDR", "bnb_idr"),
    ("DOGEIDR", "doge_idr"),
    ("ADAIDR", "ada_idr"),
    ("XRPIDR", "xrp_idr"),
    ("SOLIDR", "sol_idr"),
    ("AVAXIDR", "avax_idr"),
    ("ARBIDR", "arb_idr"),
    ("ONDOIDR", "ondo_idr"),
    ("HBARIDR", "hbar_idr"),
    ("WLDIDR", "wld_idr"),
]

FEE_INDO = 0.003   # fee taker Indodax (sesuaikan dengan akunmu, cek di web exchange)
FEE_TOKO = 0.001   # fee taker Tokocrypto (sesuaikan)
MIN_SPREAD = 0.01  # tampilkan peluang jika selisih bersih >= 1%


def indodax_price(pair_id):
    """Return (bid, ask) Indodax. Endpoint resmi: /api/ticker/{pair_id}."""
    t = _get_json(f"https://indodax.com/api/ticker/{pair_id}")["ticker"]
    return float(t["buy"]), float(t["sell"])


def tokocrypto_price(symbol):
    """Return (bid, ask) Tokocrypto.

    Endpoint market data pasca-migrasi (Des 2025):
    MBX engine -> https://www.tokocrypto.site/api/v3 (format ala Binance)
    """
    t = _get_json(
        "https://www.tokocrypto.site/api/v3/depth",
        params={"symbol": symbol},
    )
    return float(t["bids"][0][0]), float(t["asks"][0][0])


def check_opportunity(bid_i, ask_i, bid_t, ask_t):
    """Hitung keuntungan bersih (setelah fee) kedua arah."""
    beli_indo_jual_toko = (bid_t / ask_i) - 1 - FEE_INDO - FEE_TOKO
    beli_toko_jual_indo = (bid_i / ask_t) - 1 - FEE_INDO - FEE_TOKO
    return beli_indo_jual_toko, beli_toko_jual_indo


def main():
    print("Scanner arbitrase Indodax vs Tokocrypto (Ctrl+C untuk berhenti)\n")
    while True:
        for symbol, pair in PAIRS:
            try:
                bid_i, ask_i = indodax_price(pair)
                bid_t, ask_t = tokocrypto_price(symbol)
            except Exception as e:
                print(f"{symbol}: gagal ambil harga -> {e}")
                continue

            net1, net2 = check_opportunity(bid_i, ask_i, bid_t, ask_t)
            print(
                f"{symbol:8} | Indodax {bid_i:>12,.0f} / {ask_i:>12,.0f} | "
                f"Tokocrypto {bid_t:>12,.0f} / {ask_t:>12,.0f} | "
                f"net: I->T {net1*100:+.2f}% | T->I {net2*100:+.2f}%"
            )

            if net1 >= MIN_SPREAD:
                print(f"  >>> PELUANG: beli Indodax, jual Tokocrypto (net {net1*100:.2f}%)")
            if net2 >= MIN_SPREAD:
                print(f"  >>> PELUANG: beli Tokocrypto, jual Indodax (net {net2*100:.2f}%)")

        print("-" * 110)
        time.sleep(10)  # jaga rate limit API


if __name__ == "__main__":
    main()
