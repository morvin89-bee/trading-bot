"""
Arbitrase Scanner - Indodax vs Tokocrypto
Fase 1: memantau harga & mendeteksi peluang arbitrase (BELUM trading otomatis).
"""

import logging
import os
import time
import threading

import requests

logging.basicConfig(
    # ABSOLUT agar tetap menunjuk folder modul walau cwd berbeda (lihat
    # catatan main_loop.ERRORS_LOG — cacat 2026-10-20).
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "errors.log"),
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Jeda minimum antar request ke host yang sama, di seluruh proses (semua
# pemanggil `indodax_price`/`tokocrypto_price`). Tanpa pengatur ini, 13 pair
# x 2 exchange x tiap 3 detik memicu HTTP 429 dan loop tidur backoff yang
# lama, membuat satu iterasi scanner memakan belasan menit.
_ANTAR_REQUEST = threading.Lock()
_WAKTU_PERMINTAAN = {}
_JEDA_ANTAR_REQUEST = 0.15   # detik; ~6.6 request/detik per host
_JEDA_MAKS = 5.0             # batas atas jeda adaptif (jangan pernah permanen)

# Jeda AKTUAL per host — membesar otomatis tiap 429 (server diberi ruang),
# mengecil bertahap tiap sukses. Kasus nyata 2026-10-19: jeda tetap 0,15 dtk
# masih memicu 429 beruntun dari tokocrypto.site.
_jeda_host = {}


def _host(url):
    return url.split("/")[2]


def _catat_429(url):
    """Server menolak: gandakan jeda untuk host ini, sampai batas atas."""
    host = _host(url)
    dasar = _JEDA_ANTAR_REQUEST
    _jeda_host[host] = min(_JEDA_MAKS, max(dasar, _jeda_host.get(host, dasar) * 2))


def _catat_sukses(url):
    """Server mengizinkan: turunkan jeda perlahan menuju nilai dasar."""
    host = _host(url)
    sekarang = _jeda_host.get(host)
    if sekarang is None:
        return
    baru = max(_JEDA_ANTAR_REQUEST, sekarang * 0.5)
    if baru <= _JEDA_ANTAR_REQUEST:
        _jeda_host.pop(host, None)
    else:
        _jeda_host[host] = baru


def _tunggu_giliran(url):
    """Paksa jeda minimum antar request ke host yang sama (pemilik lock)."""
    host = _host(url)
    jeda = _jeda_host.get(host, _JEDA_ANTAR_REQUEST)
    now = time.monotonic()
    terakhir = _WAKTU_PERMINTAAN.get(host, 0.0)
    selisih = jeda - (now - terakhir)
    if selisih > 0:
        time.sleep(selisih)
    _WAKTU_PERMINTAAN[host] = time.monotonic()


def _get_json(url, params=None, max_retries=3):
    """GET request dengan retry + exponential backoff (termasuk HTTP 429).

    Menhormati header `Retry-After` bila server mengirimnya. Setiap
    kegagalan dicatat ke errors.log untuk pemantauan. Mengembalikan None bila
    semua percobaan habis — pemanggil memutuskan apakah data lama dipakai
    atau pair dilewati.
    """
    delay = 2.0
    last_error = None
    for attempt in range(max_retries):
        with _ANTAR_REQUEST:
            _tunggu_giliran(url)
            try:
                r = requests.get(url, params=params, timeout=10)
                if r.status_code == 429:
                    _catat_429(url)
                    retry_after = r.headers.get("Retry-After")
                    last_error = "HTTP 429 (rate limit)"
                    logging.warning(
                        "HTTP 429 dari %s, retry %d/%d setelah %.0fs",
                        url, attempt + 1, max_retries, delay,
                    )
                    print(f"  [!] HTTP 429 (rate limit) dari {url}, tunggu {delay:.0f}s")
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                else:
                    r.raise_for_status()
                    _catat_sukses(url)
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
    logging.error(
        "Menyerah setelah %d percobaan: %s (%s)", max_retries, url, last_error,
    )
    return None

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
    """Return (bid, ask) Indodax, atau None bila gagal. /api/ticker/{pair_id}."""
    data = _get_json(f"https://indodax.com/api/ticker/{pair_id}")
    if data is None:
        return None
    try:
        t = data["ticker"]
        return float(t["buy"]), float(t["sell"])
    except (KeyError, TypeError, ValueError):
        return None


def tokocrypto_price(symbol):
    """Return (bid, ask) Tokocrypto, atau None bila gagal.

    Endpoint market data pasca-migrasi (Des 2025):
    MBX engine -> https://www.tokocrypto.site/api/v3 (format ala Binance)
    """
    t = _get_json(
        "https://www.tokocrypto.site/api/v3/depth",
        params={"symbol": symbol},
    )
    if t is None:
        return None
    try:
        return float(t["bids"][0][0]), float(t["asks"][0][0])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def check_opportunity(bid_i, ask_i, bid_t, ask_t):
    """Hitung keuntungan bersih (setelah fee) kedua arah."""
    beli_indo_jual_toko = (bid_t / ask_i) - 1 - FEE_INDO - FEE_TOKO
    beli_toko_jual_indo = (bid_i / ask_t) - 1 - FEE_INDO - FEE_TOKO
    return beli_indo_jual_toko, beli_toko_jual_indo


def main():
    print("Scanner arbitrase Indodax vs Tokocrypto (Ctrl+C untuk berhenti)\n")
    while True:
        for symbol, pair in PAIRS:
            harga_i = indodax_price(pair)
            harga_t = tokocrypto_price(symbol)
            if harga_i is None or harga_t is None:
                print(f"{symbol}: gagal ambil harga (rate limit/koneksi) -> dilewati")
                continue
            bid_i, ask_i = harga_i
            bid_t, ask_t = harga_t

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
