"""Uji acak berbenih: jawaban, gangguan, dan restart dalam urutan acak.

Tanpa dependensi baru (`random` standar). Invarian diperiksa pada setiap
order yang benar-benar keluar lewat transport perekam:

1. tidak ada order tanpa `setuju` tercatat;
2. tidak ada dua order live untuk (pair, bulan DCA) yang sama;
3. tidak ada order > `MAX_ORDER_IDR`;
4. tidak ada order bila `can_withdraw` aktif atau saldo kurang;
5. maksimal 3 order per aset per hari;
6. qty jual tidak pernah melebihi kepemilikan.
"""
import random
import time

import engine
import executor
import main_loop
import rules
import safeguards
import shadow_log
from portfolio import Holding
from tests.scenarios import harness as h

PAIRS = ("btc_idr", "eth_idr", "sol_idr")
AVG = {pair: 700.0 for pair in PAIRS}   # harga 1600 -> take-profit terpicu
SEEDS = (7, 21, 42, 99)
HARI = 6


def _bulan(ts):
    return time.strftime("%Y-%m", time.localtime(ts))


def _hari(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _ctx(qty, saldo, counts, last_fill, now):
    holdings = [
        Holding(pair, pair[:-4], q, AVG[pair], h.HARGA[pair],
                q * h.HARGA[pair], q * AVG[pair],
                (h.HARGA[pair] - AVG[pair]) * q, True)
        for pair, q in qty.items() if q > 0
    ]
    hari = _hari(now)
    hari_ini = {pair: n for (pair, d), n in counts.items() if d == hari}
    held = {pair for pair, ts in last_fill.items() if now - ts <= 86400}
    return h.ctx(free_idr=saldo, holdings=holdings, today_counts=hari_ini,
                 last_fill_ts=last_fill, held_pairs_24h=held)


def _simulasikan(tmp_path, seed, monkeypatch):
    rng = random.Random(seed)
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = h.db(tmp_path, f"acak_{seed}.db")
    telegram = h.Telegram()
    transport = h.Transport()
    qty = {pair: 5.0 for pair in PAIRS}
    saldo = 600000.0
    counts = {}
    last_fill = {}
    orders = []
    dca_kunci = set()
    jam_kini = [h.ts(day=1, hour=9, minute=0)]
    diproses = [0]

    def ctx_fn(now):
        return _ctx(qty, saldo, counts, last_fill, now)

    pelari = h.Pelari(conn, telegram, transport=transport, shadow=False,
                      ctx_fn=ctx_fn, jam=jam_kini[0])

    asli = executor.submit_once

    def catat(transport_, payload, cid):
        usulan = main_loop._USULAN_MENUNGGU.get(cid)
        orders.append(dict(
            id=cid, pair=payload["pair"], type=payload["type"],
            amount=payload["amount"], price=payload["price"],
            rule=(usulan.rule if usulan is not None else "?"),
            jam=jam_kini[0]))
        return asli(transport_, payload, cid)

    monkeypatch.setattr(executor, "submit_once", catat)

    def serap():
        nonlocal saldo
        for o in orders[diproses[0]:]:
            nilai = o["amount"] * o["price"]
            # 3. atap per order
            assert nilai <= safeguards.MAX_ORDER_IDR + 1e-6, o
            # 4. izin tarik aktif = tidak boleh ada order sama sekali
            assert transport.can_withdraw is not True, o
            # 6. jual tidak melebihi kepemilikan
            if o["type"] == "sell":
                assert o["amount"] <= qty[o["pair"]] + 1e-9, (o, qty)
                qty[o["pair"]] -= o["amount"]
            else:
                # 4. saldo kurang = tidak ada order
                assert nilai <= saldo + 1e-6, (o, saldo)
                saldo -= nilai
                qty[o["pair"]] += o["amount"]
            # 5. pagu harian
            kunci = (o["pair"], _hari(o["jam"]))
            counts[kunci] = counts.get(kunci, 0) + 1
            assert counts[kunci] <= safeguards.MAX_PER_ASSET_PER_DAY, o
            last_fill[o["pair"]] = o["jam"]
            # 1. tidak ada order tanpa 'setuju' tercatat
            assert shadow_log.outcome(conn, o["id"]) in (
                "setuju", "executed"), o
            # 2. satu order live per (pair, bulan DCA)
            if o["rule"] == rules.RULE_DCA:
                kunci_dca = (o["pair"], _bulan(o["jam"]))
                assert kunci_dca not in dca_kunci, (o, dca_kunci)
                dca_kunci.add(kunci_dca)
        diproses[0] = len(orders)

    def jawab(p, aksi):
        # Detik berbeda per jawaban: id usulan = int(now * 1e6), dan dua
        # jawaban pada `now` yang sama akan menabrak PRIMARY KEY.
        jam_kini[0] += 1
        pelari.fetch.updates = [h.tombol(aksi, p.id, getattr(p, "revisi", 0))]
        pelari.denyut(jam_kini[0])
        pelari.fetch.updates = []
        serap()

    for _langkah in range(HARI * 48):          # denyut 30 menit
        jam_kini[0] += 1800
        if rng.random() < 0.08:                # restart di tengah
            h.mulai_ulang(conn, jam_kini[0])
        if rng.random() < 0.05:                # setoran masuk
            saldo += 500000.0
        telegram.gagal_kirim = rng.random() < 0.08
        transport.can_withdraw = rng.random() < 0.03
        undian = rng.random()
        pelari.verdict = (
            (lambda pair, **k: (None, 0.0)) if undian < 0.05 else
            (lambda pair, **k: (False, 99.0)) if undian < 0.12 else h.lolos)
        pelari.denyut(jam_kini[0])
        telegram.gagal_kirim = False
        serap()
        transport.can_withdraw = False

        for _ in range(2):                     # jawab maksimal dua usulan
            menunggu = list(main_loop._USULAN_MENUNGGU.values())
            if not menunggu:
                break
            p = rng.choice(menunggu)
            undian = rng.random()
            if undian < 0.55:
                aksi = "setuju"
            elif undian < 0.70 and p.rule == rules.RULE_DCA:
                aksi = "batal"
            else:
                continue
            telegram.gagal_kirim = rng.random() < 0.05
            jawab(p, aksi)
            telegram.gagal_kirim = False

    return orders


def test_uji_acak_berbenih_invarian_selalu_benar(tmp_path, monkeypatch):
    semua = []
    for seed in SEEDS:
        main_loop.reset_state()
        engine.reset_state()
        semua += _simulasikan(tmp_path / f"s{seed}", seed, monkeypatch)
    assert semua, "simulasi tidak pernah mengirim order — uji tidak bermakna"
