"""Engine satu tick: mengubah konteks menjadi daftar usulan untuk disetujui pengguna.

Modul ini tidak pernah membuka jaringan sendiri — seluruh keluaran (gerbang
spread, Telegram, transport bursa) disuntikkan pemanggil dan diuji dengan
tiruan. `tick` murni lokal dan deterministik; `on_answer` mengirim order
hanya lewat transport yang diberikan pemanggil, tidak pernah membuat kunci,
dan tidak pernah mengulang kirim. Hanya memproses aksi take-profit,
reserve buy, dan — bila sakelar per-siklus `Context.dca_enabled` menyala —
DCA bulanan (sumber v1; modul v2 tidak boleh dipanggil dari sini).
"""
from dataclasses import dataclass, field, replace
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
import time


RULE_TAKE_PROFIT = "take_profit"

# Pesan penolakan yang sudah dikirim pada satu hari lokal. Penolakan yang
# menetap (spread lebar, pagu harian, harga hilang) terulang tiap tick;
# tanpa dedupe pengguna menerima kalimat yang sama ~288 kali sehari dan
# berhenti membacanya. Kunci = (pair, kind, KODE alasan) — teks boleh
# berubah (angka spread/nominal) tanpa mengirim ulang alasan yang sama.
# Hari menjadi batas reset. Catatan shadow_log tetap ditulis setiap tick.
_PESAN_TERKIRIM = {"hari": None, "kunci": set()}


def reset_state():
    """Kosongkan dedupe pesan (dipakai tes dan `main_loop.reset_state`)."""
    _PESAN_TERKIRIM["hari"] = None
    _PESAN_TERKIRIM["kunci"].clear()


def _kirim_sekali(telegram, hari, pair, kind, kode, teks):
    """`telegram.line(teks)` paling banyak sekali per hari per alasan."""
    if _PESAN_TERKIRIM["hari"] != hari:
        _PESAN_TERKIRIM["hari"] = hari
        _PESAN_TERKIRIM["kunci"].clear()
    kunci = (pair, kind, kode)
    if kunci in _PESAN_TERKIRIM["kunci"]:
        return
    _PESAN_TERKIRIM["kunci"].add(kunci)
    telegram.line(teks)


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
      take-profit -> `RULE_TAKE_PROFIT` ("take_profit"); DCA bulanan
      (bila `Context.dca_enabled`) -> `rules.RULE_DCA` ("dca_bulanan").
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
    # Sakelar per-siklus DCA bulanan — diisi `main_loop` (satu-satunya
    # lapisan yang punya `conn` untuk membaca `dca_window`); `collect_actions`
    # tidak menerima koneksi. Default False: konteks lama tetap menghasilkan
    # perilaku v1 — tanpa sumber DCA. `collect_actions` membacanya lewat
    # `getattr(..., False)` supaya konteks bertipe `SimpleNamespace` pada tes
    # lama (yang tidak memasang field ini) tidak AttributeError.
    dca_enabled: bool = False
    # Hasil tick, bukan masukan: True bila percobaan DCA belum tuntas karena
    # konfirmasi tidak terkirim (bukan ditolak gerbang/spread). `main_loop`
    # WAJIB membiarkan bulan terbuka supaya percobaan diulang tick berikutnya;
    # menutupnya di sini akan menghabiskan jatah satu-per-bulan karena
    # gangguan Telegram yang sementara.
    dca_retry: bool = False
    # Pair DCA yang TIDAK boleh diusulkan lagi bulan ini: sudah terkirim
    # (jangan sampai ada dua usulan untuk pair+bulan yang sama) atau
    # percobaan kirimnya sudah habis (batas retry). Diisi `main_loop`.
    dca_lewati: set[str] = field(default_factory=set)
    # Memori ambang take-profit {(pair, level): (qty_target, qty_terjual,
    # last_day)} per mode; diisi `tick` dari `shadow_log` sebelum
    # `collect_actions`. State dipass masuk — `rules` tidak menyimpan apa pun.
    take_profit_sudah: dict = field(default_factory=dict)
    # Hasil tick: True bila DCA bulan ini tidak diusulkan karena saldo bebas
    # di bawah setoran. `main_loop` memakainya untuk menutup bulan tanpa
    # pesan "berakhir tanpa order" kedua — pengguna sudah diberi tahu
    # alasannya oleh `tick`.
    dca_saldo_kurang: bool = False


def collect_actions(ctx: Context, *, hari=None) -> List[rules.Action]:
    """Gabungkan take-profit, DCA bulanan (bila sakelar menyala), dan
    reserve buy, lalu satukan duplikat.

    Tiga sumber aksi yang diperbolehkan: take-profit actions, reserve buy
    actions, dan — hanya bila `ctx.dca_enabled` — DCA bulanan
    (`rules.dca_actions`). Modul v2 tidak boleh dipanggil di sini.

    Tiap aksi didaftarkan ke nama aturan sumbernya SEBELUM digabung
    (F32): `collect_actions` tahu persis aksi keluar dari daftar mana,
    jadi nama aturan dibawa sebagai asal (provenance) — bukan ditebak
    dari isi alasan. Sumber baru kelak WAJIB menambahkan pendaftaran
    namanya di sini; tanpa itu `rule_name` menolak aksinya dengan
    `ValueError`, bukan diam-diam menamai "unknown".
    """
    sumber: List[tuple[str, rules.Action]] = [
        (RULE_TAKE_PROFIT, aksi)
        for aksi in rules.take_profit_actions(
            ctx.holdings,
            sudah=getattr(ctx, "take_profit_sudah", None),
            max_order=safeguards.MAX_ORDER_IDR,
            hari=hari)
    ]
    if getattr(ctx, "dca_enabled", False):
        # Sakelar mati / field absen (konteks lama) = tanpa sumber DCA.
        # `max_order` wajib diisi dari sini: `rules` tidak boleh mengimpor
        # `safeguards` (siklus impor). Pair yang sudah terkirim atau
        # percobaan kirimnya habis dilewati: retry hanya untuk yang gagal.
        lewati = getattr(ctx, "dca_lewati", set())
        sumber += [
            (rules.RULE_DCA, aksi)
            for aksi in rules.dca_actions(
                ctx.holdings, ctx.free_idr, ctx.prices,
                max_order=safeguards.MAX_ORDER_IDR)
            if aksi.pair not in lewati
        ]
    # DCA mengklaim lebih dulu (keputusan builder #2, desain Task 15):
    # kaki reserve buy untuk (kind, pair) yang sudah diklaim DCA dilewati
    # SEBELUM `_catat_asal` berjalan — dua nama aturan untuk satu pasangan
    # adalah kebuntuan yang melempar ValueError dan men-halt seluruh tick
    # (termasuk take-profit yang tidak bersalah). Reserve buy hanya membeli
    # dari cadangan yang tersisa, jadi dilewati per-pasangan di tick ini
    # konsisten dengan keputusan itu; `rules.reserve_buy_actions` sendiri
    # tidak disentuh.
    dibeli = {(aksi.kind, aksi.pair) for nama, aksi in sumber
              if nama == rules.RULE_DCA}
    # Pair DCA yang dilewati tetap diklaim DCA: reserve buy tidak boleh
    # membeli pair yang sama sebagai order kedua di bulan yang sama.
    dibeli |= {("BUY", pair) for pair in getattr(ctx, "dca_lewati", set())}
    sumber += [
        (proposal.RULE_RESERVE_BUY, aksi)
        for aksi in rules.reserve_buy_actions(
            ctx.holdings, idr_reserve=ctx.reserve_idr)
        if (aksi.kind, aksi.pair) not in dibeli
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
    # 1. Muat memori ambang take-profit SEBELUM menilai aksi; pair yang sudah
    #    tidak dipegang ikut dibuang (posisi tertutup = siklus baru). Memori
    #    dipisah per mode: penjualan bayangan tidak menghalangi penjualan
    #    nyata, dan sebaliknya.
    mode = "shadow" if shadow else "live"
    hari = time.strftime("%Y-%m-%d", time.localtime(now))
    ctx.take_profit_sudah = shadow_log.take_profit_state(
        conn, {h.pair for h in ctx.holdings}, mode=mode)
    actions = collect_actions(ctx, hari=hari)
    # Flag hasil tick direset lebih dulu: nilai dari tick sebelumnya tidak
    # boleh membekukan keputusan bulan ini.
    ctx.dca_retry = False
    # Saldo di bawah setoran = setoran bulan ini belum ada. Satu pesan jelas
    # (bukan penolakan gerbang per pair), dan bulan TETAP TERBUKA: usulan
    # muncul otomatis begitu saldo bebas mencapai setoran, sampai batas
    # `boleh_usul_dca`. Nominal tidak dikecilkan.
    ctx.dca_saldo_kurang = bool(
        getattr(ctx, "dca_enabled", False)
        and ctx.free_idr < rules.MONTHLY_IDR)
    if ctx.dca_saldo_kurang:
        _kirim_sekali(
            telegram, hari, "-", "DCA", "saldo",
            f"Saldo bebas {alerts._rp(ctx.free_idr)} kurang dari setoran DCA "
            f"bulanan {alerts._rp(rules.MONTHLY_IDR)} — belum ada usulan. "
            "Isi saldo sebelum batas usulan bulan ini; usulan muncul "
            "otomatis begitu saldo cukup.")
    if not actions:
        return []

    proposals: List[proposal.Proposal] = []
    # Usulan yang sudah lolos semua gerbang tetapi BELUM dikirim. Konfirmasi
    # baru dikirim setelah seluruh aksi dinilai: halt di tengah tidak boleh
    # meninggalkan tombol untuk usulan yang tidak pernah dikembalikan.
    siap_kirim: List[proposal.Proposal] = []

    # Saldo berjalan: gerbang per aksi tidak boleh membaca saldo awal yang
    # sama untuk semua BUY dalam satu tick — dua order yang masing-masing
    # muat bisa bersama-sama melebihi saldo. SELL tidak mengurangi.
    saldo_bebas = ctx.free_idr

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
            _kirim_sekali(
                telegram, hari, pair, action.kind, "harga_hilang",
                f"Harga {pair} tidak tersedia — proses dihentikan untuk "
                "melindungi dana.")
            # Tidak ada konfirmasi yang terkirim pada tick ini. Bila DCA
            # sedang aktif, percobaan belum tuntas: jangan tutup bulan.
            if getattr(ctx, "dca_enabled", False):
                ctx.dca_retry = True
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
            _kirim_sekali(
                telegram, hari, pair, action.kind, "spread",
                f"Order {action.kind} {pair} tidak dieksekusi: spread "
                f"{bps_atas:.1f} bps melebihi batas "
                f"{spread.MAX_SPREAD_BPS} bps.")
            # Lanjut ke aksi berikutnya
            continue

        # 4. Gerbang safeguard
        gate_reasons = safeguards.gates_berkode(
            action,
            free_idr=saldo_bebas,
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
                    "; ".join(teks for _kode, teks in gate_reasons),
                )
            # Kirim notifikasi untuk setiap alasan penolakan (sekali per hari)
            for kode, teks in gate_reasons:
                _kirim_sekali(telegram, hari, pair, action.kind, kode, teks)
            # Lanjut ke aksi berikutnya
            continue

        # 5. Lolos kedua gerbang -> siapkan usulan (belum dikirim)
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
            # Cabang jendela (keputusan builder #1, desain Task 15): DCA
            # adalah janji 24 jam (spec §7.2) — 90 detik akan membuat hampir
            # semua Setuju hangus sebelum sempat ditekan. Sinyal reaktif
            # lainnya tetap memakai jendela pendek 90 detik.
            window_s=(proposal.DCA_WINDOW_S if rule == rules.RULE_DCA
                      else proposal.REACTIVE_WINDOW_S),
            now=now,
        )
        siap_kirim.append(prop)
        if action.kind != "SELL":
            saldo_bebas = max(0.0, saldo_bebas - action.amount_idr)

    # 6. Seluruh aksi sudah dinilai tanpa halt -> baru kirim konfirmasi.
    #    Kegagalan kirim per usulan tidak membatalkan usulan lain; untuk DCA
    #    kegagalan berarti percobaan belum tuntas (jangan tutup bulan).
    for prop in siap_kirim:
        message_id = telegram.confirmation(prop)
        if message_id is None:
            # Gagal kirim = tidak ada jendela yang dibuka dan tidak ada uang
            # bergerak. Untuk DCA ini BUKAN penolakan final: catat percobaan
            # yang gagal (dasar batas retry dan retry per pair), lalu coba
            # lagi tick berikutnya.
            if prop.rule == rules.RULE_DCA:
                ctx.dca_retry = True
                shadow_log.record(
                    conn,
                    replace(prop, id=f"undelivered_{prop.id}"),
                    "undelivered",
                    f"Konfirmasi DCA {prop.pair} tidak terkirim",
                )
            continue

        # Catat usulan sebagai "proposed" di shadow_log — juga di mode
        # eksekusi. Baris inilah yang dipulihkan `main_loop` setelah restart;
        # tanpa catatan di mode eksekusi, tombol Setuju yang sudah terkirim
        # menjadi inert saat bot hidup lagi.
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
    return shadow_log.outcome(conn, p.id)


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


def _requote_dca(conn: Any, p: proposal.Proposal, harga_sekarang: float, *,
                 now: float, telegram: telegram_gate.Telegram) -> str:
    """Re-quote DCA saat harga bergeser 0,5%-3% dari angka di pesan.

    Order DCA adalah LIMIT di `price_ref`, jadi harga limit ikut harga
    sekarang; qty dihitung dari nominal yang tetap. Id/client_order_id dan
    jendela tidak berubah. Konfirmasi BARU dikirim: persetujuan lama tidak
    dipakai karena angka yang disetujui pengguna sudah berubah. Di atas
    batas -> hangus, jendela tetap awaiting.
    """
    drift = abs(harga_sekarang - p.price_ref) / p.price_ref
    if drift > proposal.DCA_MAX_DRIFT_PCT:
        telegram.line(
            f"Harga {p.pair} bergeser {drift:.1%} dari angka di pesan, "
            f"melebihi batas re-quote DCA "
            f"{proposal.DCA_MAX_DRIFT_PCT:.0%} — konfirmasi hangus, tidak "
            "ada order. Jendela DCA tetap terbuka; usulan lama masih bisa "
            "disetujui bila harga kembali dalam 0,5%.")
        return "ignored"

    lama_harga, lama_qty = p.price_ref, p.qty
    baru = replace(
        p, price_ref=harga_sekarang, qty=p.amount_idr / harga_sekarang,
        revisi=p.revisi + 1)
    if telegram.confirmation(baru) is None:
        telegram.line(
            f"Konfirmasi re-quote {p.pair} gagal dikirim — tidak ada order. "
            "Usulan lama tetap berlaku.")
        return "ignored"

    p.price_ref, p.qty, p.revisi = baru.price_ref, baru.qty, baru.revisi
    catatan = (
        f"Re-quote DCA: harga {lama_harga} -> {harga_sekarang}, "
        f"qty {lama_qty} -> {p.qty}, nominal tetap {p.amount_idr}")
    shadow_log.perbarui_harga(conn, p, catatan)
    # Baris audit terpisah: catatan di atas akan tertimpa saat order selesai.
    shadow_log.record(
        conn,
        replace(baru, id=f"requoted_{p.id}", created_ts=now),
        "requoted",
        catatan,
    )
    telegram.line(
        f"Re-quote DCA {p.pair}: harga lama {alerts._rp(lama_harga)} -> "
        f"harga baru {alerts._rp(harga_sekarang)}, qty lama "
        f"{proposal._qty_text(lama_qty)} -> qty baru "
        f"{proposal._qty_text(p.qty)} koin. Nominal tetap "
        f"{alerts._rp(p.amount_idr)}. Tekan Setuju pada pesan konfirmasi "
        "baru untuk mengirim.")
    return "ignored"


def _catat_take_profit(conn: Any, p: proposal.Proposal, *, now: float,
                       mode: str) -> None:
    """Catat tranche take-profit yang sudah dieksekusi (bukan sekadar diusulkan).

    Dipanggil setelah order final `filled`, setelah persetujuan mode bayangan
    (simulasi jual), dan setelah status `unknown` (ragu = jangan jual dobel).
    Usulan yang ditolak/hangus tidak pernah sampai ke sini. Status final
    rejected/cancelled yang datang belakangan menghapus catatan lewat
    `_hapus_take_profit`.
    """
    if p.rule != RULE_TAKE_PROFIT or p.level is None:
        return
    target = getattr(p, "qty_target", None)
    if target is None:
        target = (p.qty or 0.0) + getattr(p, "sisa_qty", 0.0)
    shadow_log.catat_take_profit(
        conn, p.pair, p.level,
        qty_target=target, qty=p.qty or 0.0, ts=now, mode=mode,
        hari=time.strftime("%Y-%m-%d", time.localtime(now)))


def _hapus_take_profit(conn: Any, p: proposal.Proposal, *, mode: str) -> None:
    """Batalkan catatan tranche TP setelah status final rejected/cancelled."""
    if p.rule != RULE_TAKE_PROFIT or p.level is None:
        return
    shadow_log.hapus_take_profit(
        conn, p.pair, p.level, qty=p.qty or 0.0, mode=mode)


def rekonsiliasi_status(conn: Any, p: proposal.Proposal, status: str, *,
                        now: float, mode: str) -> None:
    """Terapkan status final yang baru diketahui setelah `unknown`.

    `unknown` sudah mencatat tranche sebagai selesai (ragu = jangan jual
    dobel). Bila bursa kemudian memastikan `filled`, catatan dipertahankan;
    bila `rejected`/`cancelled`, catatan tranche itu dihapus supaya level
    yang sama bisa diusulkan lagi. Tidak menyentuh jalur order sama sekali.
    """
    if status == "filled":
        _catat_take_profit(conn, p, now=now, mode=mode)
    elif status in ("rejected", "cancelled"):
        _hapus_take_profit(conn, p, mode=mode)


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
        keputusan (§6.3). PENGECUALIAN KHUSUS DCA (keputusan 2026-11-06):
        geser 0,5%-3% -> harga limit dan qty dihitung ulang dari nominal
        tetap, konfirmasi BARU dikirim, id/jendela tetap; di atas 3% tetap
        hangus.

        **KEWAJIBAN PEMANGGIL (Task 14):** fungsi ini MEMBACA
        `ctx.prices.get(p.pair)`, tidak pernah mengambil harga sendiri.
        Bila `ctx.prices` tidak diisi dari ticker, `price_still_valid`
        selalu `False` dan SETIAP konfirmasi berbalik `"ignored"` —
        gagal ke arah aman (bot diam), tetapi fitur konfirmasi jadi
        mati total dan penyebabnya gampang dicari di lapisan yang salah.
    3. Mode bayangan MENCATAT persetujuan lalu berhenti; transport tidak
       PERNAH disentuh. Di mode eksekusi persetujuan BARU dicatat pada
       langkah 6b — setelah semua gerbang lolos, tepat sebelum kirim —
       supaya penolakan spread/gerbang tidak mengunci jawaban ulang.
    4. Gerbang spread diulang pada harga SEKARANG (`verdict`) — lolos saat
       komposisi tidak menjamin lolos saat eksekusi. Harga hilang (`None`)
       = berhenti (ruling F5); spread gugur = `"gate_blocked"`.
    5. `safeguards.gates` diulang dengan konteks sekarang -> `"gate_blocked"`.
    6. `safeguards.permissions_ok` (`canWithdraw` aktif = berhenti) -> `"halted"`.
    6b. Catat jawaban PERTAMA (`setuju`) TEPAT SEBELUM transport disentuh.
       Sejak titik ini jawaban kedua terkunci — termasuk bila balasan
       pengiriman hilang dan statusnya masih `"unknown"` (R-B).
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
    mode = "shadow" if shadow else "live"
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

    # 2b. Jual tidak boleh melebihi koin yang benar-benar dipegang: usulan
    #     lahir dari konteks lama, dan kepemilikan bisa berkurang sebelum
    #     tombol ditekan. Ragu = tidak ada order.
    if p.kind == "SELL":
        dimiliki = sum(
            getattr(h, "qty", 0.0) for h in getattr(ctx, "holdings", [])
            if getattr(h, "pair", None) == p.pair)
        if p.qty is None or p.qty <= 0 or p.qty > dimiliki + 1e-9:
            telegram.line(
                f"Tolak jual {p.pair}: qty {p.qty} melebihi kepemilikan "
                f"{dimiliki:.8f} koin — tidak ada order dikirim.")
            return "gate_blocked"

    # 2c. Gerbang geser harga 0,5% (spec L116, putusan R-E): angka yang
    #     dibaca pengguna di pesan konfirmasi harus masih mewakili harga
    #     sekarang. Harga hilang/tak terbaca pun berarti TIDAK ADA
    #     keputusan (§6.3) dan `price_still_valid` menolaknya.
    harga_sekarang = ctx.prices.get(p.pair)
    if not proposal.price_still_valid(p, harga_sekarang):
        # Pengecualian KHUSUS DCA: re-quote di dalam batas, bukan hangus.
        # Harga hilang tetap jatuh ke jalur hangus di bawah (fail closed).
        if (p.rule == rules.RULE_DCA and harga_sekarang is not None
                and harga_sekarang > 0):
            return _requote_dca(conn, p, harga_sekarang, now=now,
                                telegram=telegram)
        if harga_sekarang is None or harga_sekarang <= 0:
            alasan_hangus = (
                f"Harga {p.pair} tidak bisa dibaca untuk memeriksa geseran")
        else:
            alasan_hangus = f"Harga {p.pair} bergeser dari angka di pesan"
        telegram.line(
            f"{alasan_hangus} — konfirmasi hangus, tidak ada order dikirim.")
        return "ignored"

    # 3. Mode bayangan: catat persetujuan sebagai hasilnya lalu berhenti;
    #    jalur fisik tidak pernah dijalankan dan transport tidak pernah
    #    disentuh. Mode eksekusi mencatat di langkah 6b — setelah gerbang
    #    lolos — supaya penolakan sementara tidak mengunci jawaban ulang.
    if shadow:
        _catat_hasil(
            conn, p, "setuju",
            "Mode bayangan: persetujuan dicatat, tidak ada order dikirim.")
        # Simulasi jual: ambang ini "sudah dieksekusi" di mode bayangan.
        _catat_take_profit(conn, p, now=now, mode="shadow")
        return "ignored"

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

    # 6b. Catat persetujuan PERTAMA tepat sebelum transport disentuh. Baris
    #     inilah yang mengunci jawaban kedua (langkah 0), termasuk bila
    #     balasan pengiriman hilang dan statusnya masih "unknown" — order
    #     TIDAK boleh dikirim ulang. Penolakan di langkah 4-6 tidak menulis
    #     apa pun, jadi pengguna masih bisa menekan Setuju lagi setelah
    #     gangguan sementara berlalu.
    _catat_hasil(
        conn, p, "setuju",
        "Persetujuan pertama diterima; order dikirim setelah lolos gerbang.")

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
            # Ragu = jangan jual dobel: tranche dianggap selesai sampai status
            # final membuktikan sebaliknya (rejected/cancelled menghapusnya).
            _catat_take_profit(conn, p, now=now, mode=mode)
            telegram.line(
                f"Status order {p.pair} masih belum pasti setelah tiga kali "
                "ditanya — berhenti, tidak dikirim ulang. Periksa manual di "
                f"bursa (id: {p.id}).")
            return "halted"
        # Status final -> BARU di sini portfolio.db ditulis (spec §6.6),
        # satu baris, lalu catat ke log usulan dan beri tahu pengguna.
        _tulis_status_order(conn, p, status, ts=now)
        _catat_hasil(conn, p, "executed", f"Status bursa: {status}")
        if status == "filled":
            _catat_take_profit(conn, p, now=now, mode=mode)
        elif status in ("rejected", "cancelled"):
            _hapus_take_profit(conn, p, mode=mode)
        _beritahu_status(p, status, telegram)
        return "sent"

    # 8. Tanya status dengan id yang sama (maks tiga kali, backoff [2, 5,
    #    10] — di dalam `executor.resolve_status`). Masih "unknown" ->
    #    berhenti dan beri tahu; DB disentuh nol kali.
    status = executor.resolve_status(transport, p.id)
    if status == "unknown":
        # Ragu = jangan jual dobel: tranche dianggap selesai sampai status
        # final membuktikan sebaliknya (rejected/cancelled menghapusnya).
        _catat_take_profit(conn, p, now=now, mode=mode)
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
    if status == "filled":
        _catat_take_profit(conn, p, now=now, mode=mode)
    elif status in ("rejected", "cancelled"):
        _hapus_take_profit(conn, p, mode=mode)
    _beritahu_status(p, status, telegram)
    return "sent"
