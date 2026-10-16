"""Engine satu tick: mengubah konteks menjadi daftar usulan untuk disetujui pengguna.

Modul ini tidak pernah membuka jaringan sendiri — seluruh keluaran (gerbang
spread, Telegram, transport bursa) disuntikkan pemanggil dan diuji dengan
tiruan. `tick` murni lokal dan deterministik; `on_answer` mengirim order
hanya lewat transport yang diberikan pemanggil, tidak pernah membuat kunci,
dan tidak pernah mengulang kirim. Hanya memproses aksi take-profit dan
reserve buy (modul v1).
"""
from dataclasses import dataclass
from typing import List, Callable, Any, Optional

import actions_merge
import alerts
import executor
import math
import proposal
import rules
import safeguards
import shadow_log
import spread
import sqlite3
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

    Semua titik di engine yang menyusun proposal dari aksi (gerbang
    `safeguards.gates`, catatan log bayangan, `proposal.new_proposal`)
    memanggil fungsi ini, supaya `safeguards.gates(rule=...)` dan
    `proposal.new_proposal(rule=...)` selalu menerima nilai yang **sama
    persis** (kewajiban `safeguards.py:36-38`).

    **`on_answer` (Task 12) TIDAK memanggil fungsi ini** (putusan F36):
    ia menerima `proposal.Proposal` yang sudah membawa `rule` — hasil
    pemakaian fungsi ini saat proposal dibuat. Aksi yang direkonstruksi
    dari callback tidak punya nama asal terdaftar dan akan kena
    `ValueError`; janji lama docstring ini bahwa `on_answer` akan
    memanggil `rule_name` sudah dikoreksi dan tidak boleh diwariskan
    sebagai spesifikasi.

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
    prices: dict[str, float]  # pair -> price; Task 14 WAJIB isi dari ticker
                              # (baca kewajiban pemanggil di `on_answer` 2b:
                              # harga kosong = semua konfirmasi "ignored")
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
            # Spread gugur. bps dibulatkan KE ATAS (ruling F13): 30.004
            # tampil "30.1", bukan "30" — "spread 30 bps melebihi batas
            # 30 bps" adalah kalimat yang menyalahi dirinya sendiri.
            # Angka yang sama dipakai untuk catatan log bayangan DAN
            # pesan notifikasi (kedua situs dulu sama-sama membulatkan ke
            # terdekat); ambang dibaca dari konstanta, bukan literal.
            bps_atas = math.ceil(spread_value * 10) / 10
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
                    f"Spread {bps_atas:.1f} bps melebihi batas untuk {pair}",
                )
            telegram.line(
                f"Order {action.kind} {pair} tidak dieksekusi: spread "
                f"{bps_atas:.1f} bps melebihi batas {spread.MAX_SPREAD_BPS} bps."
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


def _tulis_status_order(conn: Any, p: proposal.Proposal, status: str, *,
                        ts: float) -> None:
    """Tulis SATU baris status order final — satu-satunya tulisan portfolio.db.

    Spesifikasi §6.6: "portfolio.db hanya ditulis setelah bursa menyatakan
    terisi / batal / ditolak". Maka fungsi ini hanya dipanggil dari langkah
    terakhir `on_answer`, setelah `resolve_status` mengembalikan status
    final; setiap langkah yang gagal tidak pernah menyentuhnya.

    Bentuk tabel adalah keputusan builder (§13): satu baris per order,
    dijangkar `client_order_id` — id yang sama dipakai saat kirim dan saat
    bertanya status (§6.1), sehingga order yang sama tidak bisa menulis
    dua baris.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_status (
            client_order_id TEXT PRIMARY KEY,
            ts REAL,
            pair TEXT,
            kind TEXT,
            amount_idr REAL,
            qty REAL,
            price_ref REAL,
            rule TEXT,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO order_status (
            client_order_id, ts, pair, kind, amount_idr,
            qty, price_ref, rule, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (p.id, ts, p.pair, p.kind, p.amount_idr,
         p.qty, p.price_ref, p.rule, status),
    )
    conn.commit()


def _outcome_log(conn: Any, p: proposal.Proposal) -> Optional[str]:
    """Outcome `p.id` di `shadow_log.proposals`, atau None bila belum ada.

    Tabelnya milik `shadow_log`; bila belum pernah dibuat memang belum ada
    catatan apa pun -> None. Tidak pernah membuka jaringan — hanya membaca
    koneksi SQLite yang diberikan pemanggil.
    """
    try:
        baris = conn.execute(
            "SELECT outcome FROM proposals WHERE id = ?", (p.id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return baris[0] if baris else None


def _sudah_dijawab(conn: Any, p: proposal.Proposal) -> bool:
    """True bila `p.id` sudah pernah dijawab/dieksekusi (langkah 0, R-B).

    Pengaman utamanya `shadow_log.proposals` (kolom `id` PRIMARY KEY),
    yang mencatat `setuju`/`executed` pada jawaban PERTAMA — kini dicatat
    di kedua mode (amendmen rencana R-B). Pada kasus balasan hilang tidak
    ada baris `order_status` sama sekali, jadi guard yang hanya membaca
    tabel itu akan meleset; baris `order_status` tetap dicek sebagai
    lapis kedua, bukan satu-satunya.
    """
    if _outcome_log(conn, p) in ("setuju", "executed"):
        return True
    try:
        baris = conn.execute(
            "SELECT 1 FROM order_status WHERE client_order_id = ?",
            (p.id,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return baris is not None


def _catat_hasil(conn: Any, p: proposal.Proposal, outcome: str,
                 note: str) -> None:
    """Catat `outcome` untuk `p.id`; baris lama diperbarui, bukan gagal.

    `shadow_log.proposals.id` PRIMARY KEY dan produksi menulis id yang
    sama berulang: 'proposed' (tick, mode bayangan) -> 'setuju' (jawaban
    pertama, kini kedua mode) -> 'executed' (status final). INSERT biasa
    akan gagal keras pada tulisan kedua dan seterusnya, jadi bila baris
    sudah ada, `outcome`/`note` diperbarui — jumlah baris tetap SATU per
    usulan. Tulis barunya tetap lewat `shadow_log.record` (pemilik skema).
    """
    if _outcome_log(conn, p) is None:
        shadow_log.record(conn, p, outcome, note)
    else:
        conn.execute(
            "UPDATE proposals SET outcome = ?, note = ? WHERE id = ?",
            (outcome, note, p.id))
        conn.commit()


def _beritahu_status(p: proposal.Proposal, status: str,
                     telegram: telegram_gate.Telegram) -> None:
    """Satu baris notifikasi per perubahan status bursa (G7)."""
    if status == "filled":
        sisi = {"BUY": "beli", "SELL": "jual"}.get(p.kind, p.kind)
        telegram.line(
            f"{p.pair} {sisi} {alerts._rp(p.amount_idr)} berhasil diisi, "
            f"order_id: {p.id}")
    elif status == "cancelled":
        telegram.line(
            f"{p.pair} order {p.id} dibatalkan bursa, tidak ada posisi berubah")
    else:  # "rejected"
        telegram.line(
            f"{p.pair} order {p.id} ditolak bursa, tidak ada posisi berubah")


def on_answer(conn: Any, ctx: Context, *, p: proposal.Proposal, answer: str,
              now: float, transport: Any, telegram: telegram_gate.Telegram,
              shadow: bool,
              verdict: Callable[[str, Any],
                                tuple[bool | None, float]] = spread.spread_verdict,
              ) -> str:
    """Jawaban manusia atas satu proposal -> eksekusi SATU order, atau berhenti.

    Urutan kontrak (Task 12, putusan ronde review R-A…R-E) — setiap langkah
    yang gagal berhenti TANPA menyentuh portfolio.db:

    0. `p.id` sudah pernah dijawab/dieksekusi (baca `shadow_log.proposals`;
       pada balasan hilang memang tidak ada baris `order_status`) ->
       `"ignored"` + pemberitahuan, SEBELUM menyentuh transport (R-B).
    1. `answer` bukan "setuju" -> `"ignored"` (tunda/batal = tidak ada tindakan).
    2. Jendela konfirmasi habis (`proposal.is_expired`) -> `"ignored"`.
    2b. Harga sekarang bergeser > 0,5% dari angka di pesan
        (`proposal.price_still_valid`) -> `"ignored"` — konfirmasi hangus
        (spec L116, putusan R-E); harga tak terbaca juga berarti tidak ada
        keputusan (§6.3).

        **KEWAJIBAN PEMANGGIL (Task 14):** fungsi ini MEMBACA
        `ctx.prices.get(p.pair)`, tidak pernah mengambil harga sendiri.
        Bila `ctx.prices` tidak diisi dari ticker, `price_still_valid`
        selalu `False` dan SETIAP konfirmasi berbalik `"ignored"` —
        gagal ke arah aman (bot diam), tetapi fitur konfirmasi jadi
        mati total dan penyebabnya gampang dicari di lapisan yang salah.
    3. Catat jawaban PERTAMA pada KEDUA mode (`shadow_log.proposals` —
       amendmen rencana R-B); mode bayangan berhenti di sini, transport
       tidak PERNAH disentuh.
    4. Gerbang spread diulang pada harga SEKARANG (`verdict`) — lolos saat
       komposisi tidak menjamin lolos saat eksekusi. Harga hilang (`None`)
       = berhenti (ruling F5); spread gugur = `"gate_blocked"`.
    5. `safeguards.gates` diulang dengan konteks sekarang -> `"gate_blocked"`.
    6. `safeguards.permissions_ok` (`canWithdraw` aktif = berhenti) -> `"halted"`.
    7. `executor.submit_once` SATU KALI; `SubmissionUncertain` -> TANYA
       status dengan id yang sama (maks 3 kali, backoff [2, 5, 10]) TANPA
       PERNAH mengirim ulang (putusan R-C; plan:539, spec §6.1/L398):
       status final -> tulis satu baris DB + "executed" -> `"sent"`;
       masih `"unknown"` -> `"halted"`, DB disentuh nol kali.
    8. `executor.resolve_status` dengan id yang sama; "unknown" ->
       `"halted"` + notifikasi, DB disentuh nol kali; status final ->
       BARU di sini tulis satu baris ke portfolio.db + catat "executed"
       -> `"sent"`.

    Nama aturan dibaca dari `p.rule` (putusan F36): proposal sudah membawa
    nilai persis yang dipakai `tick` saat `safeguards.gates` dan
    `shadow_log` pertama kali. Menghitung ulang lewat `rule_name` akan kena
    `ValueError`, karena aksi yang direkonstruksi dari callback tidak
    punya nama asal terdaftar — janji docstring lama bahwa `on_answer`
    akan memanggil `rule_name` sudah dikoreksi.

    Returns:
        "sent" | "ignored" | "halted" | "gate_blocked" — sesuai kontrak.
    """
    # 0. Jawaban ganda = SATU order (putusan R-B): bila `p.id` sudah pernah
    #    dijawab/dieksekusi, berhenti SEBELUM menyentuh transport. Pengaman
    #    utamanya `shadow_log.proposals` — pada balasan hilang tidak ada
    #    baris `order_status`, jadi guard yang hanya membaca tabel itu akan
    #    meleset dan jawaban kedua akan mengirim order kedua dengan
    #    `client_order_id` yang sama persis.
    if _sudah_dijawab(conn, p):
        telegram.line(
            f"Usulan ini sudah dijawab — jawaban diabaikan, tidak ada order "
            f"baru (id: {p.id}).")
        return "ignored"

    # 1. Bukan persetujuan -> tidak ada tindakan sama sekali.
    if answer != telegram_gate.BUTTON_SETUJU:
        return "ignored"

    # 2. Jendela konfirmasi habis -> hangus; angka lama tidak pernah
    #    dipakai ulang (spec §6.2/§7.1).
    if proposal.is_expired(p, now):
        return "ignored"

    # 2b. Gerbang geser harga 0,5% (spec L116, putusan R-E): angka yang
    #     dibaca pengguna di pesan konfirmasi harus masih mewakili harga
    #     sekarang. Harga hilang/tak terbaca pun berarti TIDAK ADA
    #     keputusan (§6.3) dan `price_still_valid` menolaknya.
    harga_sekarang = ctx.prices.get(p.pair)
    if not proposal.price_still_valid(p, harga_sekarang):
        if harga_sekarang is None or harga_sekarang <= 0:
            alasan_hangus = (
                f"Harga {p.pair} tidak bisa dibaca untuk memeriksa geseran")
        else:
            alasan_hangus = f"Harga {p.pair} bergeser dari angka di pesan"
        telegram.line(
            f"{alasan_hangus} — konfirmasi hangus, tidak ada order dikirim.")
        return "ignored"

    # 3. Catat jawaban PERTAMA pada KEDUA mode (amendmen rencana R-B):
    #    baris inilah yang membuat jawaban kedua berhenti di langkah 0.
    #    Mode bayangan MENCATAT persetujuan sebagai hasilnya lalu berhenti;
    #    jalur fisik tidak pernah dijalankan dan transport tidak pernah
    #    disentuh.
    if shadow:
        _catat_hasil(
            conn, p, "setuju",
            "Mode bayangan: persetujuan dicatat, tidak ada order dikirim.")
        return "ignored"
    _catat_hasil(
        conn, p, "setuju",
        "Persetujuan pertama diterima; order menyusul setelah lolos gerbang.")

    # 4. Gerbang spread DIULANG pada harga sekarang (spec §8, Review
    #    Focus #3). `verdict` disuntikkan pemanggil — tes tidak pernah
    #    membuka jaringan (F25).
    spread_ok, spread_value = verdict(p.pair)
    if spread_ok is None:
        # Harga hilang = BERHENTI, bukan menolak lalu lanjut (ruling F5).
        telegram.line(
            f"Harga {p.pair} tidak tersedia — proses dihentikan untuk "
            "melindungi dana.")
        return "halted"
    if not spread_ok:
        # Menolak: bps dibulatkan KE ATAS (ruling F13) supaya angka yang
        # terbaca tidak pernah lebih memaafkan dari kenyataan; kedua
        # besaran (spread dan ambang) ditampilkan (ruling F17a).
        bps_atas = math.ceil(spread_value * 10) / 10
        telegram.line(
            f"{p.pair} tidak dieksekusi: spread {bps_atas:.1f} bps > "
            f"{spread.MAX_SPREAD_BPS} bps.")
        return "gate_blocked"

    # 5. Gerbang safeguards DIULANG dengan angka konteks sekarang (saldo,
    #    atap per order, pagu per aset per hari, jeda, anti-loop). Nama
    #    aturan dari proposal (F36) — ejaan "reserve_buy" persis sama
    #    dengan yang dipakai saat proposal dibuat (F17b), supaya gerbang
    #    saldo yang sadar aturan (F15) membuka cabang yang benar.
    aksi = rules.Action(
        kind=p.kind, pair=p.pair, amount_idr=p.amount_idr, qty=p.qty,
        price_ref=p.price_ref,
        reason=f"usulan terkonfirmasi pengguna ({p.id})", executable=True)
    gate_reasons = safeguards.gates(
        aksi,
        free_idr=ctx.free_idr,
        reserve_idr=ctx.reserve_idr,
        today_counts=ctx.today_counts,
        last_fill_ts=ctx.last_fill_ts,
        held_pairs_24h=ctx.held_pairs_24h,
        now=now,
        rule=p.rule,
    )
    if gate_reasons:
        for alasan in gate_reasons:
            telegram.line(alasan)
        return "gate_blocked"

    # 6. Izin bursa dibaca ulang tepat sebelum kirim (spec §6.6):
    #    canWithdraw aktif ATAU canTrade tidak cocok sakelar -> berhenti.
    izin = safeguards.permissions_ok(transport.can_trade,
                                     transport.can_withdraw, switch_on=True)
    if izin is not None:
        telegram.line(izin)
        return "halted"

    # 7. Kirim SATU KALI dengan client order id tetap = id proposal
    #    (<= 36 karakter, charset [0-9A-Za-z_-] — terdokumentasi di
    #    proposal.py, F18). Balasan hilang -> `SubmissionUncertain` ->
    #    TANYA status dengan id yang sama (putusan R-C; plan:539 "retry
    #    adalah tanggung jawab query status (Task 7)", spec §6.1/L398
    #    "maks 3 kali bertanya, jeda [2, 5, 10]") TANPA PERNAH mengirim
    #    ulang dan TANPA mengarang status (prinsip ragu = tidak kirim).
    payload = executor.payload_for(p, p.id)
    try:
        executor.submit_once(transport, payload, p.id)
    except executor.SubmissionUncertain:
        telegram.line(
            f"Balasan pengiriman order {p.pair} hilang — tidak dikirim "
            f"ulang; status ditanya ke bursa (id: {p.id}).")
        status = executor.resolve_status(transport, p.id)
        if status == "unknown":
            telegram.line(
                f"Status order {p.pair} masih belum pasti setelah tiga kali "
                "ditanya — berhenti, tidak dikirim ulang. Periksa manual di "
                f"bursa (id: {p.id}).")
            return "halted"
        # Status final -> BARU di sini portfolio.db ditulis (spec §6.6),
        # satu baris, lalu catat ke log usulan dan beri tahu pengguna.
        _tulis_status_order(conn, p, status, ts=now)
        _catat_hasil(conn, p, "executed", f"Status bursa: {status}")
        _beritahu_status(p, status, telegram)
        return "sent"

    # 8. Tanya status dengan id yang sama (maks tiga kali, backoff [2, 5,
    #    10] — di dalam `executor.resolve_status`). Masih "unknown" ->
    #    berhenti dan beri tahu; DB disentuh nol kali.
    status = executor.resolve_status(transport, p.id)
    if status == "unknown":
        telegram.line(
            f"Status order {p.pair} tidak pasti setelah tiga kali ditanya "
            "— berhenti, tidak dikirim ulang. Periksa manual di bursa "
            f"(id: {p.id}).")
        return "halted"

    # Status final dari bursa -> BARU di sini portfolio.db ditulis
    # (spec §6.6), satu baris, lalu catat ke log usulan dan beri tahu
    # pengguna (G7: satu baris notifikasi per perubahan status).
    _tulis_status_order(conn, p, status, ts=now)
    _catat_hasil(conn, p, "executed", f"Status bursa: {status}")
    _beritahu_status(p, status, telegram)
    return "sent"
