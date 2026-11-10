"""Model proposal: apa yang dimintakan persetujuan sebelum uang bergerak (§6.2).

Proposal adalah satu usulan order tunggal yang dikirim ke Telegram untuk
dikonfirmasi orang. Isi pesan konfirmasi wajib lengkap delapan field —
pair, sisi beli/jual, nominal IDR, jumlah koin, harga limit, aturan yang
memicu, perkiraan fee 0,2%, dan sisa cadangan — supaya keputusan "setuju"
dibuat dari angka yang jujur. Dua pagar menggantungkan konfirmasi:
kadaluarsa jendela (`is_expired`) dan geser harga > 0,5%
(`price_still_valid`). Modul ini murni teks dan angka — tidak membuka
jaringan, tidak punya dan tidak pernah mengirim kemampuan order.
"""
from dataclasses import dataclass
import hashlib
import re

import alerts
import rules

REACTIVE_WINDOW_S = 90      # jendela konfirmasi sinyal reaktif (§6.2)
DCA_WINDOW_S = 86400        # jendela konfirmasi DCA — 24 jam (§7.2)
PRICE_DRIFT_TOL = 0.005     # geser harga > 0,5% -> konfirmasi hangus (§6.2)
DCA_MAX_DRIFT_PCT = 0.03    # batas re-quote KHUSUS DCA (keputusan 2026-11-06):
                            # 0,5% < geser <= 3% -> harga/qty dihitung ulang;
                            # di atas 3% konfirmasi hangus, jendela tetap awaiting
FEE_PCT = 0.002             # perkiraan fee taker yang diketengahkan (§6.2)
# Batas `client_order_id` yang terdokumentasi (F18): <= 36 karakter dari
# [0-9A-Za-z_-]. Awalan terbaca dipertahankan; sisanya hash id PENUH.
CLIENT_ID_MAX = 36
CLIENT_ID_PREFIX_MAX = 20
RULE_RESERVE_BUY = "reserve_buy"   # ejaan yang sama persis dengan yang
# dibaca `safeguards.gates` (ruling F15); jangan diubah sepihak — ejaan
# lain diam-diam jatuh ke gerbang saldo DCA dan menolak reserve buy sah


@dataclass
class Proposal:
    id: str
    kind: str
    pair: str
    amount_idr: float
    qty: float
    price_ref: float
    rule: str
    reserve_after: float
    window_s: int
    created_ts: float
    status: str
    # Take-profit: ambang pemicu + basis biayanya (informasi, bukan kunci
    # memori lagi) dan koin yang tidak dijual karena atap per order.
    level: float | None = None
    basis: float | None = None
    qty_target: float | None = None
    sisa_qty: float = 0.0
    # Nomor revisi pesan konfirmasi. Re-quote menaikkannya; callback dari
    # pesan lama (revisi lebih kecil) ditolak. `client_order_id` tetap = id.
    revisi: int = 0
    # Id yang dikirim ke bursa (hash deterministik dari `id` penuh).
    # `None` = usulan lama sebelum kolom ini ada: pakai `id` apa adanya.
    client_order_id: str | None = None


def client_order_id_for(pid: str) -> str:
    """Id kirim <= 36 karakter: awalan terbaca + hash id PENUH.

    Pemotongan lama (`raw[:36]`) membuat dua id usulan yang berbagi 36
    karakter pertama menjadi SATU `client_order_id`; bursa lalu menganggap
    order kedua duplikat. Hash id penuh memisahkannya, awalan tetap terbaca.
    """
    bersih = re.sub(r"[^0-9A-Za-z_-]", "-", str(pid))
    awalan = bersih[:CLIENT_ID_PREFIX_MAX]
    sisa = CLIENT_ID_MAX - len(awalan) - 1
    return f"{awalan}-{hashlib.sha256(str(pid).encode('utf-8')).hexdigest()[:sisa]}"


def new_proposal(action: rules.Action, *, rule: str, reserve_after: float,
                 window_s: int, now: float) -> Proposal:
    """Usulan baru dari satu `action`; angka disalin apa adanya, tanpa cache.

    `rule` adalah nama aturan pemicu (mis. `"reserve_buy"`,
    `RULE_RESERVE_BUY`) — bukan bagian dari `rules.Action`, dan ejaannya
    harus persis yang dibaca `safeguards.gates`.

    `id` = `{kind}_{pair}_{mikrodetik}` dari `int(now * 1_000_000)`:

    - `now` berbeda walau selisihnya di bawah satu detik -> id berbeda
      (dua tick dalam detik yang sama, mis. `apply_ulang` vs denyut
      jantung, tidak boleh berbagi satu jangkar tombol);
    - `now` yang **identik** menghasilkan id yang identik — disengaja,
      demi determinisme tes;
    - dua aksi (kind, pair) dari tick yang sama persis menghasilkan id
      yang sama; penyatuan tiket kembar adalah tugas `merge_actions`
      (Task 4, **belum dibangun** — jangan mengandalkan id sebagai
      pengganti penyatuan itu).

    `client_order_id` (<=36 karakter dari `[0-9A-Za-z_-]`) dihitung
    `client_order_id_for(id)` — hash id PENUH, bukan potongan 36 karakter
    pertama. Diverifikasi `test_id_sub_second_distinct_and_client_order_id_safe`
    dan `test_client_order_id_hash_memisahkan_id_yang_dulu_terpotong_sama`.
    """
    id_usulan = f"{action.kind.lower()}_{action.pair}_{int(now * 1_000_000)}"
    return Proposal(
        id=id_usulan,
        kind=action.kind,
        pair=action.pair,
        amount_idr=action.amount_idr,
        qty=action.qty,             # disalin apa adanya; boleh None bila
        price_ref=action.price_ref, # aksi datang tanpa angka (lihat
                                    # price_still_valid yang menolaknya)
        rule=rule,
        reserve_after=reserve_after,
        window_s=window_s,
        created_ts=now,
        status="proposed",          # kosakata yang sama dengan shadow_log
        level=getattr(action, "level", None),
        basis=getattr(action, "basis", None),
        qty_target=getattr(action, "qty_target", None),
        sisa_qty=getattr(action, "sisa_qty", 0.0),
        client_order_id=client_order_id_for(id_usulan),
    )


def _qty_text(qty: float | None) -> str:
    """Jumlah koin dalam notasi yang dibaca orang awam — tanpa notasi ilmiah.

    `f"{3e-05}"` menghasilkan "3e-05"; pesan yang memutuskan uang tidak
    boleh memakai angka yang tidak terbaca orang awam. Dibulatkan ke 8
    desimal (presisi aset di bursa <= 8), nol ekor dibuang, tetapi satu
    digit desimal selalu dipertahankan: 275.0 tetap "275.0", 0.00003
    menjadi "0.00003".

    `qty` yang hilang (None) tampil apa adanya — maknanya belum diputuskan
    dan ditunda ke Task 12; yang pasti bukan nol.
    """
    if qty is None:
        return str(qty)
    s = f"{qty:.8f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def confirmation_text(p: Proposal) -> str:
    """Satu blok Bahasa Indonesia berisi delapan field wajib (§6.2).

    Angka rupiah lewat `alerts._rp` supaya seragam ("Rp 275.000"). Baris
    penutup menyebut sisa waktu, supaya pengguna tahu betapa singkatnya
    jendela keputusannya.
    """
    sisi = {"BUY": "BELI", "SELL": "JUAL"}.get(p.kind, p.kind)
    fee_label = f"{FEE_PCT:.1%}".replace(".", ",")   # gaya Indonesia: "0,2%"
    return (
        f"{sisi} {p.pair}\n"
        f"Nominal: {alerts._rp(p.amount_idr)}\n"
        f"Jumlah: {_qty_text(p.qty)} koin\n"
        f"Harga limit: {alerts._rp(p.price_ref)}\n"
        f"Aturan: {p.rule}\n"
        f"Perkiraan fee {fee_label}: {alerts._rp(p.amount_idr * FEE_PCT)}\n"
        f"Sisa cadangan: {alerts._rp(p.reserve_after)}\n"
        f"Berlaku {p.window_s} detik — jawab di dalam waktunya."
        + (f"\nSisa {_qty_text(p.sisa_qty)} koin dijadwalkan sebagai "
           "tranche berikutnya (dipotong ke atap per order)."
           if getattr(p, "sisa_qty", 0) else "")
    )


def is_expired(p: Proposal, now: float) -> bool:
    """True bila jendela konfirmasi sudah habis; tepat batas = hangus.

    `>= window_s` artinya detik ke-90 sudah kadaluarsa — konservatif,
    sesuai prinsip "ragu = tidak kirim".
    """
    return now - p.created_ts >= p.window_s


def price_still_valid(p: Proposal, current_price: float | None) -> bool:
    """False bila harga bergeser lebih dari `PRICE_DRIFT_TOL` (§6.2/§6.3).

    Harga hilang (`None`) atau terbaca Rp 0 bukan berarti nol — itu berarti
    **tidak ada keputusan** (§6.3), jadi selalu False. `price_ref` yang
    hilang/nol dari aksi sumber juga menolak: tidak ada angka pembanding
    berarti tidak ada konfirmasi yang jujur.
    """
    if current_price is None or current_price <= 0:
        return False
    if p.price_ref is None or p.price_ref <= 0:
        return False
    return abs(current_price - p.price_ref) / p.price_ref <= PRICE_DRIFT_TOL
