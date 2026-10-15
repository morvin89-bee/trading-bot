"""Gerbang per-aksi: batas uang dan izin untuk SATU order (§6.4, §6.6).

Sebelum sebuah aksi diusulkan — dan sebelum dikirim — gerbang ini menilainya
sendirian: atap per order, pagu per aset per hari, jeda setelah terisi,
anti-loop beli/jual 24 jam, dan saldo bebas setelah cadangan. Modul ini
murni membaca angka yang disuntikkan pemanggil; tidak punya dan tidak
pernah mengirim kemampuan order.

Hasilnya daftar alasan berbahasa Indonesia: kosong = lolos, satu pun alasan
berarti order TIDAK dikirim (ragu = tidak kirim) dan alasan itulah yang
dipakai untuk memberi tahu pengguna — tidak ada penolakan diam-diam.
"""
import alerts
import rules

MAX_ORDER_IDR = 275000            # atap per order (Rp)
MAX_PER_ASSET_PER_DAY = 3         # pagu per ASET per hari (bukan global)
COOLDOWN_SECONDS = 1800           # jeda 30 menit setelah satu order terisi


def gates(action: rules.Action, *, free_idr, reserve_idr,
          today_counts: dict[str, int], last_fill_ts: dict[str, float],
          held_pairs_24h: set[str], now: float) -> list[str]:
    """Alasan penolakan `action` berbahasa Indonesia; kosong = lolos.

    Urutan pemeriksaan (spec §6.4): atap per order -> pagu per aset hari ini
    -> jeda setelah terisi -> anti-loop beli/jual 24 jam -> saldo bebas
    (`free - cadangan`). Semua alasan dikumpulkan, bukan berhenti di yang
    pertama, supaya pesan notifikasi menyebut semua pelanggaran sekaligus.
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

    tersedia = free_idr - reserve_idr          # available = free - cadangan
    if tersedia < action.amount_idr:
        reasons.append(
            f"Tolak {pair}: saldo bebas {alerts._rp(free_idr)} dikurangi "
            f"cadangan {alerts._rp(reserve_idr)} = {alerts._rp(tersedia)}, "
            f"kurang dari {alerts._rp(action.amount_idr)}.")

    return reasons


def permissions_ok(can_trade: bool, can_withdraw: bool,
                   switch_on: bool) -> str | None:
    """Kalimat halt berbahasa Indonesia, atau `None` bila semua cocok (§6.6).

    `canWithdraw` True = berhenti apa pun kondisi lain (izin tarik dana
    tidak boleh ada). `canTrade` harus sama dengan status sakelar: bayangan
    menyala + `canTrade` True = ada kunci trade terpasang padahal belum
    waktunya; sakelar nyala + `canTrade` False = eksekusi mustahil jalan.
    """
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
