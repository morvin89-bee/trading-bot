"""Engine satu tick: mengubah konteks menjadi daftar usulan untuk disetujui pengguna.

Modul ini murni dan deterministik — tidak membuka jaringan, tidak pernah
mengirim kemampuan order. Semua keputusan bersifat lokal dan dapat diuji.
Hanya memproses aksi take-profit dan reserve buy (modul v1).
"""
from dataclasses import dataclass
from typing import List, Callable, Any, Optional

import actions_merge
import proposal
import rules
import safeguards
import shadow_log
import spread
import telegram_gate


RULE_TAKE_PROFIT = "take_profit"
# Tanda pengenal aksi reserve buy pada `rules.Action.reason` —
# `rules.reserve_buy_actions` selalu menulis "beli pakai sebagian cadangan".
_TANDA_CADANGAN = "cadangan"


def rule_name(action: rules.Action) -> str:
    """Nama aturan yang menghasilkan `action` — satu-satunya sumber nama aturan.

    Semua titik di engine yang membutuhkan nama aturan (gerbang
    `safeguards.gates`, catatan log bayangan, `proposal.new_proposal`, dan
    kelak `on_answer` pada Task 12) memanggil fungsi ini, supaya
    `safeguards.gates(rule=...)` dan `proposal.new_proposal(rule=...)`
    selalu menerima nilai yang **sama persis** (kewajiban
    `safeguards.py:36-38`).

    Hasil:

    - Aksi reserve buy (BUY yang alasan-nya menyebut pemakaian cadangan)
      -> `proposal.RULE_RESERVE_BUY`, yaitu tepat `"reserve_buy"` — ejaan
      yang dibaca `safeguards.gates` (ruling F15). Salah ejaan membuat
      gerbang saldo memakai aturan `free - reserve`, yang menolak SETIAP
      reserve buy (cadangan adalah sumber dananya) dan membuat sistem tak
      pernah bisa lolos syarat nol penolakan (spec §9).
    - Aksi take-profit (SELL) -> `RULE_TAKE_PROFIT` ("take_profit"), nama
      yang stabil dan berarti, bukan kata pertama dari alasan berbahasa
      Indonesia yang bisa berubah bebas.
    - Selain itu -> "unknown": nama yang tidak dikenal tidak pernah
      membuka cabang cadangan, jadi gagalnya selalu ke arah yang aman
      (ragu = tidak kirim).
    """
    if action.kind == "BUY" and _TANDA_CADANGAN in action.reason:
        return proposal.RULE_RESERVE_BUY
    if action.kind == "SELL":
        return RULE_TAKE_PROFIT
    return "unknown"


@dataclass
class Context:
    """Konteks yang diperlukan engine untuk satu tick."""
    holdings: List[Any]  # rules.Holding objects
    prices: dict[str, float]  # pair -> price
    free_idr: float
    reserve_idr: float
    today_counts: dict[str, int]  # pair -> count
    last_fill_ts: dict[str, float]  # pair -> timestamp
    held_pairs_24h: set[str]  # set of pairs


def collect_actions(ctx: Context) -> List[rules.Action]:
    """Gabungkan take-profit dan reserve buy, lalu satukan duplikat.

    Hanya dua sumber aksi yang diperbolehkan v1: take-profit actions
    dan reserve buy actions. Modul v2 tidak boleh dipanggil di sini.
    """
    take_profit = rules.take_profit_actions(ctx.holdings)
    reserve_buy = rules.reserve_buy_actions(
        ctx.holdings, idr_reserve=ctx.reserve_idr
    )
    raw_actions = take_profit + reserve_buy
    return actions_merge.merge_actions(raw_actions)


def tick(
    conn: Any,
    ctx: Context,
    *,
    now: float,
    telegram: telegram_gate.Telegram,
    shadow: bool,
    verdict: Callable[[str, Any], tuple[bool | None, float]] = spread.spread_verdict,
) -> List[proposal.Proposal]:
    """Satu tick engine: dari konteks ke daftar usulan.

    Args:
        conn: Koneksi basis data SQLite untuk shadow_log
        ctx: Konteks engine
        now: Timestamp saat ini (detik sejak epoch)
        telegram: Objek Telegram untuk mengirim notifikasi
        shadow: Jika True, catat ke shadow_log
        verdict: Fungsi gerbang spread (di-inject untuk keperluan testing)

    Returns:
        Daftar Proposal yang siap dikonfirmasi pengguna.
        Kosong jika ada halt (verdict None) atau tidak ada usulan.
    """
    # 1. Kumpulkan dan satukan aksi
    actions = collect_actions(ctx)
    if not actions:
        return []

    proposals: List[proposal.Proposal] = []

    # 2. Proses setiap aksi
    for action in actions:
        pair = action.pair
        # Nama aturan dihitung SATU KALI dari satu sumber (F31): gerbang,
        # catatan bayangan, dan usulan tidak boleh berbeda ejaan.
        rule = rule_name(action)

        # 3. Gerbang spread
        spread_ok, spread_value = verdict(pair)
        
        if spread_ok is None:
            # Harga hilang = BERHENTI
            if shadow:
                shadow_log.record(
                    conn,
                    proposal.Proposal(
                        id=f"halt_{pair}_{int(now * 1_000_000)}",
                        kind=action.kind,
                        pair=pair,
                        amount_idr=0.0,
                        qty=0.0,
                        price_ref=0.0,
                        rule="halt",
                        reserve_after=0.0,
                        window_s=0,
                        created_ts=now,
                        status="halted",
                    ),
                    "halted",
                    f"Harga hilang untuk pair {pair}",
                )
            telegram.line(
                f"Harga {pair} tidak tersedia — proses dihentikan untuk melindungi dana."
            )
            # BERHENTI segera, tidak proses aksi lain
            return []
        
        if not spread_ok:
            # Spread gugur
            if shadow:
                # Buat proposal dummy untuk logging
                dummy_proposal = proposal.Proposal(
                    id=f"spread_rej_{pair}_{int(now * 1_000_000)}",
                    kind=action.kind,
                    pair=pair,
                    amount_idr=action.amount_idr,
                    qty=action.qty or 0.0,
                    price_ref=action.price_ref or 0.0,
                    rule=rule,
                    reserve_after=ctx.reserve_idr,
                    window_s=0,
                    created_ts=now,
                    status="spread_rejected",
                )
                shadow_log.record(
                    conn,
                    dummy_proposal,
                    "spread_rejected",
                    f"Spread {spread_value:.0f} bps melebihi batas untuk {pair}",
                )
            telegram.line(
                f"Order {action.kind} {pair} tidak dieksekusi: spread "
                f"{spread_value:.0f} bps melebihi batas 30 bps."
            )
            # Lanjut ke aksi berikutnya
            continue

        # 4. Gerbang safeguard
        gate_reasons = safeguards.gates(
            action,
            free_idr=ctx.free_idr,
            reserve_idr=ctx.reserve_idr,
            today_counts=ctx.today_counts,
            last_fill_ts=ctx.last_fill_ts,
            held_pairs_24h=ctx.held_pairs_24h,
            now=now,
            rule=rule,  # nama aturan tunggal: gerbang dan usulan selalu sama
        )
        
        if gate_reasons:
            # Ditolak oleh gerbang
            if shadow:
                # Buat proposal dummy untuk logging
                dummy_proposal = proposal.Proposal(
                    id=f"gate_rej_{pair}_{int(now * 1_000_000)}",
                    kind=action.kind,
                    pair=pair,
                    amount_idr=action.amount_idr,
                    qty=action.qty or 0.0,
                    price_ref=action.price_ref or 0.0,
                    rule=rule,
                    reserve_after=ctx.reserve_idr,
                    window_s=0,
                    created_ts=now,
                    status="gate_rejected",
                )
                shadow_log.record(
                    conn,
                    dummy_proposal,
                    "gate_rejected",
                    "; ".join(gate_reasons),
                )
            # Kirim notifikasi untuk setiap alasan penolakan
            for reason in gate_reasons:
                telegram.line(reason)
            # Lanjut ke aksi berikutnya
            continue

        # 5. Lolos kedua gerbang -> buat usulan
        # Hitung sisa cadangan setelah reserve buy (jika berlaku)
        reserve_after = ctx.reserve_idr
        if rule == proposal.RULE_RESERVE_BUY:
            # Cadangan berkurang sesuai amount_idr yang dipakai. Dulu tesnya
            # "reserve_buy" in action.reason — syarat yang tak pernah terpenuhi
            # (alasan berisi "beli pakai sebagian cadangan", bukan "reserve_buy"),
            # jadi sisa cadangan tak pernah berkurang. Sekarang memakai nama
            # aturan yang sama dengan gerbang dan usulan.
            reserve_after = max(0.0, ctx.reserve_idr - action.amount_idr)

        # Buat proposal sebenarnya
        prop = proposal.new_proposal(
            action,
            rule=rule,
            reserve_after=reserve_after,
            window_s=proposal.REACTIVE_WINDOW_S,  # Default window untuk sinyal reaktif
            now=now,
        )
        
        # Kirim pesan konfirmasi dengan tombol inline
        message_id = telegram.confirmation(prop)
        if message_id is None:
            # Gagalkirim konfirmasi, lanjut ke aksi berikutnya
            continue

        # Catat usulan sebagai "proposed" di shadow_log
        if shadow:
            shadow_log.record(conn, prop, "proposed", "")

        proposals.append(prop)

    return proposals