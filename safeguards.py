"""Gerbang per-aksi: batas uang dan izin untuk SATU order (§6.4, §6.6).

Sebelum sebuah aksi diusulkan — dan sebelum dikirim — gerbang ini menilainya
sendirian: atap per order, pagu per aset per hari, jeda setelah terisi,
anti-loop beli/jual 24 jam, dan gerbang saldo yang sadar jenis aksi
(ruling F15 — lihat docstring `gates`). Modul ini
murni membaca angka yang disuntikkan pemanggil; tidak punya dan tidak
pernah mengirim kemampuan order.

Hasilnya daftar alasan berbahasa Indonesia: kosong = lolos, satu pun alasan
berarti order TIDAK dikirim (ragu = tidak kirim) dan alasan itulah yang
dipakai untuk memberi tahu pengguna — tidak ada penolakan diam-diam.
"""
import alerts
import rules

# Atap per order = 55% setoran bulanan. Diturunkan dari `rules.MONTHLY_IDR`
# supaya menaikkan setoran tidak diam-diam membuat DCA satu pair menembus atap.
MAX_ORDER_IDR = int(rules.MONTHLY_IDR * 0.55)
MAX_PER_ASSET_PER_DAY = 3         # pagu per ASET per hari (bukan global)
COOLDOWN_SECONDS = 1800           # jeda 30 menit setelah satu order terisi
RESERVE_MAX_FRACTION = 0.5        # bagian cadangan yang boleh dipakai satu
                                  # reserve buy (§10.1); ditegakkan di sini
                                  # juga, bukan hanya di reserve_buy_actions


def gates(action: rules.Action, *, free_idr, reserve_idr,
          today_counts: dict[str, int], last_fill_ts: dict[str, float],
          held_pairs_24h: set[str], now: float,
          rule: str = "") -> list[str]:
    """Alasan penolakan `action` berbahasa Indonesia; kosong = lolos.

    Urutan pemeriksaan (spec §6.4): atap per order -> pagu per aset hari ini
    -> jeda setelah terisi -> anti-loop beli/jual 24 jam -> gerbang saldo.
    Semua alasan dikumpulkan, bukan berhenti di yang pertama, supaya pesan
    notifikasi menyebut semua pelanggaran sekaligus.

    `rule` adalah nama aturan yang menghasilkan aksi (mis. "reserve_buy"),
    konsisten dengan `proposal.new_proposal(action, *, rule, ...)` — bukan
    bagian dari `rules.Action`.

    Gerbang saldo sadar jenis aksi (ruling F15): aturan `free - reserve`
    tanpa syarat akan menolak **semua** SELL (sell tidak membelanjakan IDR,
    sell *menghasilkan* IDR) dan membuat reserve buy **mustahil** (cadangan
    adalah sumber dananya) — melumpuhkan seluruh sistem sambil terlihat
    lulus semua tesnya. Maka:

    - **SELL** -> gerbang saldo tidak berlaku sama sekali.
    - **BUY dengan `rule == "reserve_buy"`** -> yang boleh dipakai
      `(free_idr - reserve_idr) + RESERVE_MAX_FRACTION * reserve_idr`.
      `free_idr` SUDAH termasuk cadangan; menambahkan seluruh
      `RESERVE_MAX_FRACTION * reserve_idr` di atas `free_idr` menghitung
      cadangan dua kali dan meloloskan order yang menguras lebih dari
      separuh. Batas `max_fraction` ditegakkan **di gerbang ini juga**,
      bukan hanya di `reserve_buy_actions` (pertahanan kedua).
    - **BUY lainnya** (DCA, take-profit beli) -> `free_idr - reserve_idr`.
    """
    reasons = []
    pair = action.pair

    if action.amount_idr > MAX_ORDER_IDR:
        reasons.append(
            f"Tolak {pair}: nominal {alerts._rp(action.amount_idr)} "
            f"melebihi atap per order {alerts._rp(MAX_ORDER_IDR)}.")

    hari_ini = today_counts.get(pair, 0)
    if hari_ini >= MAX_PER_ASSET_PER_DAY:
        reasons.append(
            f"Tolak {pair}: sudah {hari_ini} order hari ini, pagu "
            f"{MAX_PER_ASSET_PER_DAY} order per aset per hari.")

    terisi = last_fill_ts.get(pair)
    if terisi is not None and now - terisi < COOLDOWN_SECONDS:
        reasons.append(
            f"Tolak {pair}: jeda {COOLDOWN_SECONDS // 60} menit setelah "
            f"order terisi belum selesai.")

    if pair in held_pairs_24h:
        reasons.append(
            f"Tolak {pair}: aset ini kena jeda 24 jam karena sudah ada "
            f"beli/jual dalam 24 jam terakhir.")

    if action.kind != "SELL":     # ruling F15: sell tidak membelanjakan IDR
        if rule == "reserve_buy":
            tersedia = ((free_idr - reserve_idr)
                        + RESERVE_MAX_FRACTION * reserve_idr)
            sumber_dana = (f"saldo bebas {alerts._rp(free_idr)} dikurangi "
                           f"cadangan {alerts._rp(reserve_idr)}, ditambah "
                           f"cadangan maksimal "
                           f"{RESERVE_MAX_FRACTION:.0%}")
        else:
            tersedia = free_idr - reserve_idr   # available = free - cadangan
            sumber_dana = (f"saldo bebas {alerts._rp(free_idr)} dikurangi "
                           f"cadangan {alerts._rp(reserve_idr)}")
        if tersedia < action.amount_idr:
            reasons.append(
                f"Tolak {pair}: {sumber_dana} = {alerts._rp(tersedia)}, "
                f"kurang dari {alerts._rp(action.amount_idr)}.")

    return reasons


def permissions_ok(can_trade: bool | None, can_withdraw: bool | None,
                   switch_on: bool) -> str | None:
    """Kalimat halt berbahasa Indonesia, atau `None` bila semua cocok (§6.6).

    `canWithdraw` True = berhenti apa pun kondisi lain (izin tarik dana
    tidak boleh ada). `canTrade` harus sama dengan status sakelar: bayangan
    menyala + `canTrade` True = ada kunci trade terpasang padahal belum
    waktunya; sakelar nyala + `canTrade` False = eksekusi mustahil jalan.

    Tiga keadaan: `None` berarti izin TIDAK TERBACA (mis. bursa tidak
    mengembalikan field-nya). Ragu = berhenti; `None` tidak boleh jatuh ke
    cabang "cocok" hanya karena kebetulan tidak sama dengan `switch_on`.
    """
    if can_trade is None or can_withdraw is None:
        return ("Berhenti: izin API tidak terbaca (canTrade/canWithdraw "
                "tidak diketahui) — ragu berarti tidak ada order.")
    if can_withdraw:
        return ("Berhenti: izin tarik dana (canWithdraw) aktif — izin itu "
                "tidak boleh ada pada kunci API.")
    if can_trade != switch_on:
        if can_trade:
            return ("Berhenti: sakelar mati tetapi kunci masih bisa trading "
                    "(canTrade) — ada kunci trade terpasang padahal belum "
                    "waktunya.")
        return ("Berhenti: sakelar aktif tetapi kunci tidak bisa trading "
                "(canTrade) — eksekusi tidak akan bisa jalan, periksa kunci "
                "API.")
    return None
