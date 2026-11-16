"""Koneksi akun Indodax lewat TAPI - mode BACA SAJA, auto-detect v1/v2.

Pemakaian:
    import indodax_api
    indodax_api.my_trades("btc_idr")   # auto-detect versi API
    indodax_api.account_info()

Persiapan:
    1. Buat API key di https://indodax.com/trade_api
    2. Izinkan HANYA "Reading (View Only)" (jangan Spot Trading/Withdraw)
    3. Isi INDODAX_KEY dan INDODAX_SECRET di file .env

Keamanan: .env berisi rahasia. Jangan dibagikan atau diunggah.

Dokumentasi: btcid/indodax-official-api-docs
  TAPI v1: POST https://indodax.com/tapi        (HMAC-SHA512, header Key/Sign)
  TAPI v2: GET  https://api.indodax.com/api/v2  (HMAC-SHA256, header X-APIKEY/Sign)
"""
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

V1_URL = "https://indodax.com/tapi"
V2_BASE = "https://api.indodax.com"
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# WAF Indodax memblokir User-Agent bawaan Python (respons HTML 403).
# Pakai UA browser agar permintaan lolos ke lapisan API.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_WORKING = None  # "v1" atau "v2" setelah terdeteksi


def _load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _creds():
    env = _load_env()
    key = os.environ.get("INDODAX_KEY") or env.get("INDODAX_KEY", "")
    secret = os.environ.get("INDODAX_SECRET") or env.get("INDODAX_SECRET", "")
    return key, secret


def _http_error(e):
    """Pesan galat HTTP dari badan balasan — HANYA untuk `HTTPError`.

    `HTTPError` adalah satu-satunya eksepsi `urllib` yang punya `.read()`;
    memanggilnya pada `URLError` menutupi sebab asli (lihat `_galat_jaringan`).
    """
    body = e.read().decode(errors="replace")
    try:
        err = json.loads(body)
        msg = err.get("msg") or err.get("error") or body[:100]
    except Exception:
        msg = body[:100]
    return f"HTTP {e.code}: {msg}"


def _galat_jaringan(e):
    """Sebab ASLI galat jaringan dalam satu baris pendek, tanpa rahasia.

    Cacat nyata 2026-11-11 (`errors.log`): `_http_error` dipanggil untuk
    SETIAP eksepsi, jadi `URLError` (DNS gagal, koneksi diputus, timeout)
    berakhir sebagai `"'URLError' object has no attribute 'read'"` — pesan
    yang terbaca operator tentang `read`, bukan tentang jaringan yang mati.
    Klasifikasi di sini menyebut DNS/reset/timeout apa adanya.
    """
    if isinstance(e, urllib.error.HTTPError):
        return _http_error(e)
    alasan = getattr(e, "reason", None) or e
    if isinstance(alasan, socket.gaierror):
        return "DNS gagal: nama host tidak ditemukan"
    if isinstance(alasan, (socket.timeout, TimeoutError)):
        return "timeout: bursa tidak menjawab"
    if isinstance(alasan, ConnectionResetError) or getattr(
            alasan, "winerror", None) == 10054:
        return "koneksi diputus bursa (reset)"
    if isinstance(alasan, ConnectionRefusedError):
        return "koneksi ditolak"
    return f"jaringan: {alasan}"


def _v1_call(method, **params):
    """TAPI v1 POST with HMAC‑SHA512.
    Implements up to 3 retries with exponential back‑off (+ jitter).
    Melempar ``RuntimeError`` dengan sebab asli setelah semua percobaan gagal:
    pemanggil menangkapnya (`my_trades`/`account_info`) atau membungkusnya
    menjadi `SubmissionUncertain` (`executor.submit_once`) — tidak pernah
    diam-diam mengembalikan ``None``.
    """
    key, secret = _creds()
    max_tries = 3
    delay = 1.0
    import random
    galat = "tidak ada percobaan"
    for attempt in range(1, max_tries + 1):
        # Timestamp dan tanda tangan dihitung PER PERCOBAAN. Cacat 2026-11-16:
        # keduanya dibekukan di depan loop, jadi percobaan ketiga membawa
        # timestamp berumur ~3-4 detik dan bisa ditolak "Invalid timestamp ...
        # outside of the recvWindow" sebelum permintaannya sampai.
        data = {"method": method, "timestamp": int(time.time() * 1000)}
        data.update(params)
        body = urllib.parse.urlencode(data)
        sign = hmac.new(secret.encode(), body.encode(),
                        hashlib.sha512).hexdigest()
        req = urllib.request.Request(
            V1_URL, data=body.encode(),
            headers={"Key": key, "Sign": sign, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            galat = _galat_jaringan(e)
            if attempt < max_tries:
                time.sleep(delay + random.random() * 0.5)
                delay *= 2
    raise RuntimeError(f"v1 {galat}")


def _v2_get(path, params=None):
    """TAPI v2: GET dengan tanda tangan HMAC-SHA256."""
    key, secret = _creds()
    data = dict(params or {})
    # recvWindow eksplisit 15 dtk (2026-10-20): dua penolakan
    # "Invalid timestamp ... outside of the recvWindow" terjadi saat jam mesin
    # hanya meleset 0,8 dtk — permintaan bertanda tertahan di jalur lambat
    # (telegram_gate sempat tidur 10 dtk). Tanpa parameter ini validitas
    # sepenuhnya diserahkan ke bawaan server yang tidak terdokumentasi.
    data["recvWindow"] = 15000
    data["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(data)
    sign = hmac.new(secret.encode(), query.encode(),
                    hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{V2_BASE}{path}?{query}",
        headers={"Accept": "application/json",
                 "X-APIKEY": key,
                 "Sign": sign,
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"v2 {_http_error(e)}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"v2 {_galat_jaringan(e)}")


def _parse_date(ds):
    ds = (ds or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(ds, fmt)))
        except ValueError:
            continue
    return int(time.time())


def _unwrap_v1(resp, pair):
    r = resp.get("return") or {}
    if isinstance(r, dict):
        if isinstance(r.get(pair), list):
            return r[pair]
        for v in r.values():
            if isinstance(v, list):
                return v
    elif isinstance(r, list):
        return r
    return []


def _unwrap_v2(resp, pair):
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("data", "result", "return", pair.replace("_", "")):
            if isinstance(resp.get(k), list):
                return resp[k]
    return []


def _normalize_v1(raw):
    """Format v1 -> format baku."""
    out = []
    for t in raw:
        try:
            price = float(t.get("price") or 0)
            qty = float(t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not price or not qty:
            continue
        out.append({
            "id": str(t.get("tid") or t.get("id")
                      or t.get("trade_id") or ""),
            "side": str(t.get("type") or t.get("side") or "").lower(),
            "price": price,
            "qty": qty,
            "quote": qty * price,
            "time": _parse_date(str(t.get("date") or "")),
        })
    return out


def _normalize_v2(raw):
    """Format v2 -> format baku."""
    out = []
    for t in raw:
        try:
            price = float(t.get("price") or 0)
            qty = float(t.get("qty") or t.get("quantity")
                          or t.get("amount") or 0)
            quote = float(t.get("quoteQty") or t.get("total")
                            or (price * qty))
        except (TypeError, ValueError):
            continue
        if not price or not qty or not quote:
            continue
        side = ""
        if "isBuyer" in t:
            side = "buy" if t["isBuyer"] else "sell"
        else:
            for f in ("side", "type"):
                v = str(t.get(f) or "").lower()
                if v in ("buy", "b"):
                    side = "buy"
                elif v in ("sell", "s"):
                    side = "sell"
                if side:
                    break
        ms = t.get("time") or t.get("timeMs") or 0
        try:
            ts = int(ms) // 1000 if ms else int(time.time())
        except (TypeError, ValueError):
            ts = int(time.time())
        out.append({
            "id": str(t.get("id") or t.get("tradeId")
                      or t.get("tid") or ""),
            "side": side,
            "price": price,
            "qty": qty,
            "quote": quote,
            "time": ts,
        })
    return out


def my_trades(pair):
    """Riwayat transaksi untuk satu pair (auto-detect TAPI v1/v2).

    Mengembalikan list of dict:
      {id, side, price, qty, quote, time}
    """
    key, secret = _creds()
    if not key or not secret:
        raise RuntimeError(
            "API key belum diisi. Buat key di "
            "https://indodax.com/trade_api (izin: Reading/View Only), "
            "lalu isi INDODAX_KEY/INDODAX_SECRET di .env")
    global _WORKING
    errors = []
    if _WORKING != "v1":
        try:
            resp = _v2_get("/api/v2/myTrades",
                           {"symbol": pair.replace("_", ""), "limit": 100})
            trades = _normalize_v2(_unwrap_v2(resp, pair))
            _WORKING = "v2"
            return trades
        except Exception as e:
            errors.append(str(e))
    if _WORKING != "v2":
        try:
            resp = _v1_call("tradeHistory", pair=pair)
            if not resp.get("success"):
                raise RuntimeError(
                    f"API menolak: {resp.get('error')}")
            trades = _normalize_v1(_unwrap_v1(resp, pair))
            _WORKING = "v1"
            return trades
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors)[:200])


def account_info():
    """Informasi akun & saldo (auto-detect v1/v2)."""
    global _WORKING
    errors = []
    if _WORKING != "v1":
        try:
            resp = _v2_get("/api/v2/account",
                           {"omitZeroBalances": "true"})
            _WORKING = "v2"
            return resp
        except Exception as e:
            errors.append(str(e))
    if _WORKING != "v2":
        try:
            resp = _v1_call("getInfo")
            if not resp.get("success"):
                raise RuntimeError(
                    f"API menolak: {resp.get('error')}")
            _WORKING = "v1"
            return resp
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors)[:200])
