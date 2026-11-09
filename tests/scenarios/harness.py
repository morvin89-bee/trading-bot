"""Harness skenario lintas hari — tanpa jaringan, tanpa jam dinding.

Semua waktu disuntikkan sebagai `now` (epoch detik, waktu lokal); tidak ada
`time.sleep`, `time.time`, atau transport nyata. Transport dan Telegram hanya
perekam, jadi skenario bisa menggerakkan denyut lima menit selama berhari-hari
dengan cepat dan deterministik.
"""
import os
import sqlite3
import time

import engine
import main_loop

HARGA = {"btc_idr": 1600.0, "eth_idr": 1400.0, "sol_idr": 350.0}
LANGKAH_DENYUT = 300


def ts(day=10, hour=12, minute=0):
    """Epoch dari waktu LOKAL; hari dibatasi 1-28 agar aman semua bulan."""
    kini = time.localtime()
    return time.mktime(time.struct_time(
        (kini.tm_year, kini.tm_mon, day, hour, minute, 0, 0, 0, -1)))


def db(tmp_path, nama="skenario.db"):
    conn = sqlite3.connect(os.path.join(str(tmp_path), nama))
    conn.row_factory = sqlite3.Row
    return conn


class Telegram:
    """Telegram palsu: mencatat pesan dan konfirmasi, tanpa jaringan."""

    def __init__(self):
        self.lines = []
        self.konfirmasi = []
        self.gagal_kirim = False

    def line(self, teks):
        self.lines.append(teks)
        return True

    def confirmation(self, prop):
        if self.gagal_kirim:
            return None
        self.konfirmasi.append(prop)
        return len(self.konfirmasi)

    def pesan(self, potongan):
        return [s for s in self.lines if potongan.lower() in s.lower()]


class Transport:
    """Transport bursa perekam; tidak pernah menyentuh jaringan."""

    def __init__(self, *, can_trade=True, can_withdraw=False, status="FILLED"):
        self.posted = []
        self.queried = []
        self.can_trade = can_trade
        self.can_withdraw = can_withdraw
        self.status = status

    def post_order(self, payload):
        self.posted.append(payload)
        return {"status": self.status, "order_id": len(self.posted)}

    def get_order_status(self, client_order_id):
        self.queried.append(client_order_id)
        return {"status": self.status}


class Fetch:
    """`fetch` tiruan untuk `telegram_gate.get_updates`."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.panggilan = []

    def __call__(self, token, *, offset, timeout):
        self.panggilan.append((token, offset, timeout))
        return {"result": list(self.updates)}


def tombol(aksi, id_usulan, revisi=0):
    return {"update_id": 1,
            "callback_query": {"data": f"{aksi}:{id_usulan}:{revisi}"}}


def ctx(*, free_idr=600000.0, harga=None, holdings=None, reserve_idr=0.0,
        today_counts=None, last_fill_ts=None, held_pairs_24h=None):
    return engine.Context(
        holdings=list(holdings or []),
        prices=dict(HARGA if harga is None else harga),
        free_idr=free_idr,
        reserve_idr=reserve_idr,
        today_counts=dict(today_counts or {}),
        last_fill_ts=dict(last_fill_ts or {}),
        held_pairs_24h=set(held_pairs_24h or set()),
    )


def lolos(pair, **kwargs):
    return (True, 1.0)


class Pelari:
    """Menggerakkan `main_loop.tick_cycle` tiap lima menit dari `jam`."""

    def __init__(self, conn, telegram, *, ctx_fn=None, transport=None,
                 shadow=True, verdict=lolos, fetch=None,
                 pembaca_konteks=None, jam=None):
        self.conn = conn
        self.telegram = telegram
        self.ctx_fn = ctx_fn or (lambda now: ctx())
        self.transport = transport
        self.shadow = shadow
        self.verdict = verdict
        self.fetch = fetch if fetch is not None else Fetch()
        self.pembaca_konteks = pembaca_konteks
        self.jam = ts() if jam is None else jam

    def denyut(self, now=None, **ubah):
        if now is not None:
            self.jam = now
        akses = dict(
            now=self.jam, telegram=self.telegram, ctx=self.ctx_fn(self.jam),
            transport=self.transport, shadow=self.shadow,
            verdict=self.verdict, fetch=self.fetch,
            pembaca_konteks=self.pembaca_konteks,
        )
        akses.update(ubah)
        return main_loop.tick_cycle(self.conn, **akses)

    def sampai(self, akhir, langkah=LANGKAH_DENYUT):
        """Jalankan denyut sampai `akhir` (inklusif)."""
        hasil = []
        while self.jam <= akhir:
            hasil.append(self.denyut())
            self.jam += langkah
        return hasil


def mulai_ulang(conn, now):
    """Simulasi proses mati lalu hidup: state memori dibuang, DB dipulihkan."""
    main_loop.reset_state()
    main_loop._muat_usulan(conn, now=now)
    main_loop._muat_offset(conn)
    return main_loop._USULAN_MENUNGGU
