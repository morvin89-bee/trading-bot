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


def _catat_asal(peta: dict, action: rules.Action, nama: str) -> None:
    """Daftarkan asal `action` ke `peta` dengan nama aturan `nama`.

    `peta` memetakan `(kind, pair)` ke nama aturan sumbernya (F32). Dua
    sumber berbeda yang menghasilkan pasangan yang sama adalah kebuntuan
    penamaan: salah satu pasti salah, dan pilihan diam-diam bisa jatuh ke
    arah yang tidak aman — "reserve_buy" yang keliru MEMBUKA cabang
    cadangan di `safeguards.gates`. Maka error keras, bukan diam-diam
    mengambil yang terakhir.
    """
    kunci = (action.kind, action.pair)
    lama = peta.get(kunci)
    if lama is not None and lama != nama:
        raise ValueError(
            f"Aksi {action.kind} {action.pair} diklaim dua aturan berbeda: "
            f"{lama!r} dan {nama!r}. Tetapkan SATU nama aturan untuk "
            "sumber yang bertabrakan ini sebelum aksi diproses."
        )
    peta[kunci] = nama


def rule_name(action: rules.Action) -> str:
    """Nama aturan yang menghasilkan `action` — satu-satunya sumber nama aturan.

    Semua titik di engine yang membutuhkan nama aturan (gerbang
    `safeguards.gates`, catatan log bayangan, `proposal.new_proposal`, dan
    kelak `on_answer` pada Task 12) memanggil fungsi ini, supaya
    `safeguards.gates(rule=...)` dan `proposal.new_proposal(rule=...)`
    selalu menerima nilai yang **sama persis** (kewajiban
    `safeguards.py:36-38`).

    Hasil (F32 — nama diambil dari ASAL aksi, bukan dari kata di dalam
    alasan):

    - Aksi keluaran `collect_actions` -> nama yang didaftarkan sumbernya
      lewat `_catat_asal`: reserve buy -> `proposal.RULE_RESERVE_BUY`
      ("reserve_buy", ejaan yang dibaca `safeguards.gates`, ruling F15);
      take-profit -> `RULE_TAKE_PROFIT` ("take_profit").
    - Alasan berbahasa Indonesia TIDAK dibaca sama sekali: redaksi alasan
      boleh berubah (Task 13 menulis alasan DCA sendiri) tanpa mengubah
      nama aturan, dan aksi lain yang kebetulan menyebut "cadangan" tidak
      bisa salah dinamai `reserve_buy` — arah keliru yang MEMBUKA cabang
      cadangan dan mempertaruhkan uang.
    - Aksi tanpa nama terdaftar (bukan keluaran `collect_actions`, atau
      sumber baru yang belum mendaftarkan dirinya) -> `ValueError`, bukan
      "unknown". Nama "unknown" akan tampil ke pengguna ("Aturan:
      unknown") dan masuk `shadow_log` dengan diam-diam, melanggar G4
      (setiap aksi tertelusuri ke SATU aturan). Gagal keras memaksa
      keputusan sadar.
    """
    nama = getattr(action, "_nama_aturan", None)
    if nama is None:
        raise ValueError(
            f"Aksi {action.kind} {action.pair} tidak punya nama aturan "
            "terdaftar — hanya keluaran collect_actions yang boleh "
            "diproses engine. Daftarkan sumbernya lebih dulu (F32)."
        )
    return nama


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

    Tiap aksi didaftarkan ke nama aturan sumbernya SEBELUM digabung
    (F32): `collect_actions` tahu persis aksi keluar dari daftar mana,
    jadi nama aturan dibawa sebagai asal (provenance) — bukan ditebak
    dari isi alasan. Sumber baru kelak (Task 13, DCA) WAJIB menambahkan
    pendaftaran namanya di sini; tanpa itu `rule_name` menolak aksinya
    dengan `ValueError`, bukan diam-diam menamai "unknown".
    """
    sumber: List[tuple[str, rules.Action]] = [
        (RULE_TAKE_PROFIT, aksi)
        for aksi in rules.take_profit_actions(ctx.holdings)
    ] + [
        (proposal.RULE_RESERVE_BUY, aksi)
        for aksi in rules.reserve_buy_actions(
            ctx.holdings, idr_reserve=ctx.reserve_idr
        )
    ]
    peta: dict = {}
    for nama, aksi in sumber:
        _catat_asal(peta, aksi, nama)

    merged = actions_merge.merge_actions([aksi for _, aksi in sumber])
    # Dipasang SETELAH merge: untuk grup kembar, `merge_actions` membuat
    # objek `rules.Action` BARU (alasan digabung, angka dasar disalin), jadi
    # pemasangan sebelum merge akan hilang. Kunci (kind, pair) setiap grup
    # sama persis dengan kuncinya di `peta`, jadi asalnya tetap terbaca.
    for aksi in merged:
        aksi._nama_aturan = peta[(aksi.kind, aksi.pair)]
    return merged


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
        # catatan bayangan, dan usulan tidak boleh berbeda ejaan. Namanya
        # berasal dari asal aksi di collect_actions, bukan dari alasan (F32).
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