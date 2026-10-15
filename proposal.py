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

import alerts
import rules

REACTIVE_WINDOW_S = 90      # jendela konfirmasi sinyal reaktif (§6.2)
DCA_WINDOW_S = 86400        # jendela konfirmasi DCA — 24 jam (§7.2)
PRICE_DRIFT_TOL = 0.005     # geser harga > 0,5% -> konfirmasi hangus (§6.2)
FEE_PCT = 0.002             # perkiraan fee taker yang diketengahkan (§6.2)
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


def new_proposal(action: rules.Action, *, rule: str, reserve_after: float,
                 window_s: int, now: float) -> Proposal:
    """Usulan baru dari satu `action`; angka disalin apa adanya, tanpa cache.

    `rule` adalah nama aturan pemicu (mis. `"reserve_buy"`,
    `RULE_RESERVE_BUY`) — bukan bagian dari `rules.Action`, dan ejaannya
    harus persis yang dibaca `safeguards.gates`.

    `id` dibentuk deterministik dari (kind, pair, detik pembuatan): unik
    untuk tiap usulan dalam satu tick karena `merge_actions` menyisakan
    tepat satu aksi per (kind, pair), dan berubah tiap tick. Dipakai sebagai
    jangkar tombol Telegram (`"{tombol}:{id}"`) dan jejak log bayangan.
    """
    return Proposal(
        id=f"{action.kind.lower()}_{action.pair}_{int(now)}",
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
    )


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
        f"Jumlah: {p.qty} koin\n"
        f"Harga limit: {alerts._rp(p.price_ref)}\n"
        f"Aturan: {p.rule}\n"
        f"Perkiraan fee {fee_label}: {alerts._rp(p.amount_idr * FEE_PCT)}\n"
        f"Sisa cadangan: {alerts._rp(p.reserve_after)}\n"
        f"Berlaku {p.window_s} detik — jawab di dalam waktunya."
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
