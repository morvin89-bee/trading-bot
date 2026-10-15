"""Gerbang spread: lebar spread orderbook sebagai syarat eksekusi (§8).

Sebelum apa pun diusulkan untuk dieksekusi, spread `pair` diukur saat itu
juga dari orderbook publik Indodax. Spread melebihi `MAX_SPREAD_BPS` ->
tidak dieksekusi otomatis. Data hilang/rusak berarti TIDAK ada keputusan
(`None`, bukan 0) — harga yang hilang tidak boleh dinilai nol lalu
diloloskan (spec 6.3). Modul ini hanya membaca; tidak memiliki dan tidak
pernah mengirim kemampuan order.
"""
import json
import math
import urllib.error
import urllib.request

from indodax_api import UA

MAX_SPREAD_BPS = 30        # 0,30% = 30 basis point
DEPTH_URL = "https://indodax.com/api/depth/{pair_id}"
TIMEOUT = 20               # detik


def _best_prices(depth):
    """`(bid, ask)` float dari sisi terbaik tiap sisi orderbook.

    Bisa raise (`TypeError`/`KeyError`/`IndexError`/`ValueError`) bila
    `depth` tidak berbentuk; pemanggil yang menangkap.
    """
    bid = float(depth["buy"][0][0])
    ask = float(depth["sell"][0][0])
    return bid, ask


def spread_bps(depth):
    """Spread dalam basis point: `(ask - bid) / mid x 10_000`.

    `depth` punya kunci `buy`/`sell`, masing-masing daftar `[price, amount]`.
    """
    bid, ask = _best_prices(depth)
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10_000


def fetch_depth(pair_id):
    """Orderbook publik `pair_id` (baca-saja); `None` bila gagal/429/kosong."""
    req = urllib.request.Request(
        DEPTH_URL.format(pair_id=pair_id),
        headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            body = r.read().decode()
    except (urllib.error.URLError, OSError):
        return None                      # termasuk 429 (HTTPError)
    try:
        depth = json.loads(body)
    except ValueError:
        return None
    return depth or None                 # body kosong -> None


def spread_verdict_from_depth(depth, max_bps=MAX_SPREAD_BPS):
    """Inti murni gerbang: `(lolos?, spread_bps)` dari `depth`.

    Data hilang/rusak atau harga terbaca Rp 0 -> `(None, nan)`: tidak ada
    keputusan, bukan lolos dengan angka nol (spec 6.3). Ambang dinilai dari
    nilai mentah (`> max_bps` berarti gugur, §8); angka yang dikembalikan
    dibulatkan 1 desimal untuk pelaporan.
    """
    try:
        bid, ask = _best_prices(depth)
        raw = spread_bps(depth)
    except (TypeError, KeyError, IndexError, ValueError, ZeroDivisionError):
        return None, float("nan")
    if math.isnan(raw) or bid <= 0 or ask <= 0:
        return None, float("nan")
    return raw <= max_bps, round(raw, 1)


def spread_verdict(pair_id, max_bps=MAX_SPREAD_BPS, *, fetch=fetch_depth):
    """Nilai spread `pair_id` lewat `fetch` yang disuntikkan.

    `fetch` (bawaan: `fetch_depth`) harus mengembalikan `depth` atau `None`
    bila gagal — inilah titik disuntikkan engine supaya tes tidak pernah
    membuka jaringan.
    """
    return spread_verdict_from_depth(fetch(pair_id), max_bps)
