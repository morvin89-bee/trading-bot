"""Loop utama: denyut jantung, sakelar mode bayangan, dan integrasi modul.

Modul ini tidak pernah membuka jaringan, tidak pernah membaca `.env` untuk
kunci dagang, dan tidak memiliki kemampuan menempatkan order. Semua jalur
keluar — konteks pasar, gerbang spread, Telegram, dan jalur ke bursa —
disuntikkan pemanggil; `main()` sendiri hanya bisa membangun objek bursa
lewat `transport_factory` yang diberikan, dan tanpa factories itu ia tidak
membangun apa pun.

Urutan `tick_cycle` (berhenti di titik pertama yang gagal):

1. Rekonsiliasi `portfolio.db` vs bursa — selisih apa pun = berhenti, SEBELUM
   ada usulan apa pun (spec §6.3/§6.6).
2. Izin bursa dibaca ulang: `canWithdraw` aktif = berhenti apa pun.
3. **Polling Telegram.** Proses ini adalah SATU-SATUNYA konsumen `getUpdates`
   di seluruh program; tanpa langkah ini gerbang bersifat tulis-saja dan
   setiap konfirmasi akan kedaluwarsa tanpa pernah terjawab (spec §7.1 butir 4,
   putusan F6).
4. Pengingat pasif DCA — maksimal satu pesan per slot jam (kabel T-13.1).
5. `engine.tick` — Proposals, dan tidak ada lain.
6. Gerbang mode bayangan: satu penolakan = reset ke nol; hari bayangan naik
   **maksimal sekali per hari lokal**.

Sakelar naik kelas hanya setelah `GO_LIVE_SHADOW_DAYS` hari bayangan BERSIH.
Angka itu tidak pernah ditulis inline di perbandingan: spec §9 menyebut rentang
7-14 hari, dan 7 adalah lantainya.

Keterbatasan yang disengaja dan perlu diketahui pembaca berikutnya:

- `telegram_gate` hanya bisa mengirim pesan; tidak ada fungsi untuk mengedit
  tombol pesan lama. Karena itu "mematikan keyboard" di sini berarti mengirim
  satu baris yang menyatakan tombolnya tidak berlaku lagi. Pengaman yang
  sesungguhnya ada di `engine.on_answer` langkah 0 (jawaban kedua berhenti
  sebelum menyentuh bursa) — sudah dipatok tesnya sendiri di sana.
- `main()` tidak membangun konteks pasar: repo ini belum punya pembaca data
  pasar yang disuntikkan ke `engine.Context`. Tanpa `ctx`, loop berjalan dan
  tidak mengusulkan apa pun — diam dan benar, bukan menebak.
"""
import argparse
import dataclasses
import time
import types

import dca_window
import engine
import indodax_api
import portfolio
import portfolio_store
import reconcile
import rules
import safeguards
import scheduler
import shadow_log
import spread
import telegram_gate


# Denyut jantung: lima menit (plan:1116).
HEARTBEAT_S = 300

# Jam aktif DITURUNKAN dari `dca_window.DND` (23, 7) — bukan daftar jam kedua.
# Menyalinnya berarti dua sumber kebenaran yang bisa melenceng satu jam.
ACTIVE_HOURS = (dca_window.DND[1], dca_window.DND[0])   # (7, 23)

# Lantai syarat sakelar (spec §9: "7-14 hari mode bayangan"). Nama, bukan literal.
GO_LIVE_SHADOW_DAYS = 7

# Keadaan proses (bukan persistensi):
#   usulan yang menunggu jawaban pengguna — dipolling, bukan diambil dari DB,
#   karena `engine.on_answer` butuh objek Proposal lengkap.
_USULAN_MENUNGGU = {}
# Slot pengingat yang sudah dipakai: (tanggal lokal, jam).
_SLOT_PENGINGAT = set()
# Offset getUpdates terakhir — pemanggilan berikutnya hanya membaca yang baru.
_OFFSET_TELEGRAM = {"offset": 0}


def reset_state():
    """Kosongkan seluruh keadaan proses (dipakai tes, dan oleh `main`)."""
    _USULAN_MENUNGGU.clear()
    _SLOT_PENGINGAT.clear()
    _OFFSET_TELEGRAM["offset"] = 0


def load_state(conn):
    """Baca state bot dari tabel `bot_state` milik `dca_window`.

    Mengembalikan dict berisi `v1_enabled` (bool), `shadow_day` (int), dan
    `last_dca_date` (str "YYYY-MM" atau None).

    WAJIB membaca lewat `dca_window.ensure_bot_state` — membuat tabel atau
    pembuat state kedua dilarang keras (putusan R-G): dua bentuk skema yang
    berbeda akan membuat satu modul menulis dan modul lain membaca kolom yang
    tidak sama.
    """
    dca_window.ensure_bot_state(conn)
    baris = conn.execute(
        "SELECT last_dca_date, v1_enabled, shadow_day FROM bot_state WHERE id = 1"
    ).fetchone()
    return {
        "last_dca_date": baris[0],
        "v1_enabled": bool(baris[1]),
        "shadow_day": int(baris[2]),
    }


def set_shadow_day(conn, day):
    """TETAPKAN `shadow_day` ke `day` — sifatnya menetapkan, bukan menambah.

    Panggilan kedua dengan angka lain MENGGANTI, bukan menambah: fungsi ini
    menetapkan keadaan, bukan menghitungnya. `v1_enabled` ikut mengikuti aturan
    yang sama di satu tempat saja, supaya tidak ada dua tempat yang memutuskan
    syarat sakelar.
    """
    dca_window.ensure_bot_state(conn)
    nyalakan = 1 if int(day) >= GO_LIVE_SHADOW_DAYS else 0
    conn.execute(
        "UPDATE bot_state SET shadow_day = ?, v1_enabled = ? WHERE id = 1",
        (int(day), nyalakan),
    )
    conn.commit()


def _hari_lokal(now_ts):
    """Tanggal lokal "YYYY-MM-DD" — satu-satunya bentuk yang dibaca log bayangan."""
    return time.strftime("%Y-%m-%d", time.localtime(now_ts))


def _tabel_ada(conn, nama):
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (nama,),
    ).fetchone()[0] > 0


def apply_shadow_gate(conn, *, now=None):
    """Terapkan aturan mode bayangan untuk satu hari lokal (spec §9).

    Dua halves, URUTANNYA mengikat:

    1. Bila ada penolakan pada HARI INI (`shadow_log.rejected_count`), hari
       bayangan di-nol-kan dan sakelar dimatikan. Pemeriksaan ini berjalan
       pada SETIAP pemanggilan — termasuk setelah hari ini sudah dihitung.
       Kalau tidak, penolakan sore hari bisa lolos hanya karena penanda hari
       sudah terisi pagi tadi.
    2. HANYA kalau penanda hari (`bot_state.shadow_checked_day`) belum sama
       dengan hari ini, `shadow_day` naik satu dan penanda diisi.

    Tanpa langkah 2, denyut jantung lima menit sekali akan menaikkan
    `shadow_day` dua belas kali sehari dan menyalakan sakelar sekitar pukul
    15.00 pada hari pertama — melewati satu-satunya syarat keamanan yang
    menjadi alasan proyek ini ada.

    `now` boleh disuntikkan (waktu lokal, detik sejak epoch). Tanpa itu
    dipakai `time.time()`.
    """
    if now is None:
        now = time.time()
    hari_ini = _hari_lokal(now)

    # Tabel proposals milik shadow_log; log bisa belum pernah dibuat, dan
    # "belum ada catatan" sama dengan "nol penolakan" — bukan galat.
    ditolak = 0
    if _tabel_ada(conn, "proposals"):
        ditolak = shadow_log.rejected_count(conn, hari_ini)

    if ditolak > 0:
        set_shadow_day(conn, 0)
        return

    dca_window.ensure_bot_state(conn)
    baris = conn.execute(
        "SELECT shadow_day, shadow_checked_day FROM bot_state WHERE id = 1"
    ).fetchone()
    if baris[1] == hari_ini:
        return   # hari ini sudah dihitung — denyut berikutnya tidak menambah
    # `set_shadow_day` satu-satunya tempat yang memutuskan `v1_enabled`.
    set_shadow_day(conn, int(baris[0]) + 1)
    conn.execute(
        "UPDATE bot_state SET shadow_checked_day = ? WHERE id = 1", (hari_ini,)
    )
    conn.commit()


def should_run_habitual(now_ts):
    """True di luar jendela DND — mendelegasikan ke `dca_window.in_dnd`.

    DND 23.00-07.00 lokal: batas awal inklusif, batas akhir eksklusif
    (23.00 masuk, 07.00 keluar). Aturan itu milik `dca_window`; menyalinnya
    di sini berarti dua tempat yang harus selalu sepakat.
    """
    return not dca_window.in_dnd(now_ts)


def _konteks_kosong():
    """Konteks tanpa posisi — dipakai `main()` yang belum punya pembaca pasar."""
    return engine.Context(
        holdings=[], prices={}, free_idr=0.0, reserve_idr=0.0,
        today_counts={}, last_fill_ts={}, held_pairs_24h=set(),
    )


def baca_konteks(conn, *, now=None, sinkron=portfolio.sync_trades,
                 ambil_info=indodax_api.account_info,
                 ambil_harga=portfolio.fetch_price):
    """Baca konteks pasar SEBENARNYA untuk satu denyut (pembaca produksi).

    Urutan dan kejujuran angkanya, sesuai rancangan periode shadow:

    1. `sinkron(conn)` — tarik riwayat trade terbaru (read-only; kegagalan per
       pair sudah ditelan di dalam `portfolio.sync_trades`).
    2. `ambil_info()` — respons MENTAH `account_info`. Hanya bentuk TAPIv2
       (`balances` list) yang dipercaya; bentuk lain = `ValueError`, karena
       tanpa bentuk yang dikenal `free` tidak bisa dipastikan (ragu = tidak
       mengambil keputusan).
    3. `ambil_harga` untuk SEMUA pair di `portfolio.COIN_PAIRS`; satu pun
       `None` = `ValueError` — tidak ada angka parsial yang menyusup ke
       denyut (harga hilang = tidak ada keputusan, spec §6.3).
    4. `free_idr` dihitung dari field `free` SAJA (bukan `free + locked`):
       saldo yang terkunci order tidak boleh dianggap tersedia (spec §6.4
       "free sudah bersih"). `reserve_idr = 0.0` — tidak ada konfigurasi
       cadangan terkunci di repo; mengarang angkanya akan membuat pesan
       berbohong.
    5. `today_counts` / `last_fill_ts` / `held_pairs_24h` diturunkan dari
       riwayat `trades` dengan WAKTU LOKAL (`time.localtime`) — bukan isi
       yang dikarang, bukan `utcnow`.

    `dca_enabled` dibiarkan default `False`: yang mengisinya per siklus
    adalah `main_loop` lewat `dca_window` (satu-satunya pemilik `conn` di
    lapisan ini).
    """
    now = time.time() if now is None else now

    sinkron(conn)

    info = ambil_info()
    if not (isinstance(info, dict) and isinstance(info.get("balances"), list)):
        raise ValueError("bentuk saldo tidak dikenal — bukan daftar balances TAPIv2")

    prices = {pair: ambil_harga(pair) for pair in portfolio.COIN_PAIRS.values()}
    hilang = [pair for pair, harga in prices.items() if harga is None]
    if hilang:
        raise ValueError("harga gagal dibaca: " + ", ".join(sorted(hilang)))

    free_idr = 0.0
    for b in info["balances"]:
        asset = str(b.get("asset") or "").strip().lower()
        if asset in ("idr", "rp"):
            try:
                free_idr += float(b.get("free") or 0)
            except (TypeError, ValueError):
                raise ValueError("nilai free IDR tidak terbaca")

    coins = {k: v for k, v in portfolio.parse_balances(info).items()
             if k != "IDR"}
    trades = portfolio_store.list_trades(conn)
    holdings = portfolio.compute_holdings(trades, coins, prices)

    hari_ini = time.strftime("%Y-%m-%d", time.localtime(now))
    today_counts: dict = {}
    last_fill_ts: dict = {}
    held_pairs_24h: set = set()
    for t in trades:
        side = (t.get("side") or "").lower()
        if side not in ("buy", "sell") or t.get("ts") is None:
            continue
        ts = float(t["ts"])
        pair = t["pair"]
        last_fill_ts[pair] = max(last_fill_ts.get(pair, ts), ts)
        if time.strftime("%Y-%m-%d", time.localtime(ts)) == hari_ini:
            today_counts[pair] = today_counts.get(pair, 0) + 1
        if now - ts <= 86400:
            held_pairs_24h.add(pair)

    return engine.Context(
        holdings=holdings, prices=prices, free_idr=free_idr,
        reserve_idr=0.0, today_counts=today_counts,
        last_fill_ts=last_fill_ts, held_pairs_24h=held_pairs_24h,
    )


def _ingat(usulan):
    """Daftarkan usulan yang menunggu jawaban supaya poling bisa merujuknya."""
    for p in usulan:
        _USULAN_MENUNGGU[p.id] = p
    return usulan


def _catat_dca(conn, now, usulan):
    """Buka atau tutup bulan DCA setelah satu tick — HANYA bila disebut.

    Usulan DCA di daftar berarti pesan benar-benar terkirim (`engine.tick`
    hanya mengembalikan yang terkirim) -> jendela resmi dibuka. DCA aktif
    tapi nol usulan terkirim = percobaan sudah habis -> bulan ditutup
    (keputusan pengguna 2026-10-17: sekali coba, gagal = dilewati).
    """
    if any(p.rule == rules.RULE_DCA for p in usulan):
        dca_window.mark_pending(conn, now)
    else:
        dca_window.mark_skip(conn, now)


def _jawab(conn, ctx, aksi, id_usulan, *, now, telegram, transport,
           shadow, verdict):
    """Teruskan satu jawaban pengguna ke `engine.on_answer`.

    `ctx.prices` WAJIB terisi dari ticker (kabel Task 12): `on_answer` membacanya
    untuk gerbang geser harga 0,5%. Kosong = setiap konfirmasi berbalik
    "ignored" tanpa alasan yang jujur.

    F46: jendela DCA hanya disentuh bila jawaban rujuk ke usulan DCA yang
    benar-benar dikenal — id tak dikenal berarti jenis usulan tidak
    diketahui, jadi ragu dan tidak menulis (lihat tes F46).
    """
    if aksi == "ulang":
        return apply_ulang(conn, ctx, now=now, telegram=telegram,
                           shadow=shadow, verdict=verdict)

    # Jawaban DCA dicatat terpisah: tombol Setuju pada keyboard konfirmasi DCA
    # harus menutup jendela 24 jam, kalau tidak pengingat tetap menyala dan di
    # +24 jam tercipta catatan `dca_missed` palsu (kabel Task 13 #7). Pencarian
    # usulan lebih dulu: tombol hanya berarti bila id-nya dikenal DAN
    # usulannya ber-rule DCA (F46) — Setuju pada take-profit tidak boleh
    # menutup jendela DCA, dan id tak dikenal tidak boleh menyentuh apa pun.
    usulan = _USULAN_MENUNGGU.get(id_usulan)
    if aksi == telegram_gate.BUTTON_SETUJU:
        if usulan is not None and usulan.rule == rules.RULE_DCA:
            dca_window.mark_confirmed(conn, now)
    elif aksi == telegram_gate.BUTTON_BATAL:
        if usulan is not None and usulan.rule == rules.RULE_DCA:
            if dca_window.mark_batal(conn, now):
                # Jendela resmi ditutup: lepas proposal dari ingatan, supaya
                # jawaban lanjutan (mis. Setuju) menempuh jalur "id tak
                # dikenal" yang inert — tanpa ini `on_answer` tetap mengirim
                # order sementara `dca_status` 'batal' (order tanpa jendela
                # yang sah). `mark_batal` False = bulan sudah selesai:
                # ingatan TIDAK dilepas, perilaku lama dipertahankan.
                _USULAN_MENUNGGU.pop(id_usulan, None)
            else:
                telegram.line(
                    "Bulan ini sudah selesai, Batal bulan ini tidak berlaku."
                )

    if usulan is None:
        telegram.line(
            f"Usulan {id_usulan} sudah tidak dikenal — tidak ada order dikirim."
        )
        return "ignored"

    # Tanpa transport di luar mode bayangan, `on_answer` tidak punya siapa pun
    # untuk ditanya (F40). Berhenti di sini, bukan melempar AttributeError.
    if not shadow and transport is None:
        telegram.line(
            "Eksekusi belum dinyalakan — jawaban dicatat, tidak ada order dikirim."
        )
        return "ignored"

    hasil = engine.on_answer(
        conn, ctx, p=usulan, answer=aksi, now=now, transport=transport,
        telegram=telegram, shadow=shadow, verdict=verdict,
    )
    # Tombol di pesan lama tidak berlaku lagi. Pengaman sesungguhnya ada di
    # langkah 0 `on_answer` (jawaban kedua berhenti sebelum menyentuh bursa);
    # pesan ini supaya pengguna tidak menekan tombol yang sudah mati.
    telegram.line(
        f"Usulan {id_usulan} sudah diproses. Tombol di pesan lama sudah tidak berlaku."
    )
    return hasil


def _polling(conn, ctx, *, now, telegram, transport, shadow, verdict, fetch):
    """Baca `getUpdates`, teruskan ke `on_answer` (satu-satunya konsumennya).

    Mengembalikan True bila ada perintah `/ulang` yang sudah menghasilkan
    usulan pada siklus ini — pemanggil lalu tidak menjalankan `engine.tick`
    lagi, karena dua usulan dari aksi dan waktu yang sama persis akan memakai
    `client_order_id` yang sama.

    Tanpa `fetch` yang disuntikkan, polling dilewati seluruhnya — berarti
    TIDAK ADA jalur keluar jaringan di loop ini.
    """
    if fetch is None:
        return False
    updates, offset_baru = telegram_gate.get_updates(
        getattr(telegram, "token", ""),
        offset=_OFFSET_TELEGRAM["offset"],
        fetch=fetch,
    )
    _OFFSET_TELEGRAM["offset"] = offset_baru
    diulang = False
    for update in updates:
        parsed = telegram_gate.parse_update(update)
        if parsed is None:
            continue
        aksi, id_usulan = parsed
        if aksi == "ulang":
            diulang = True
        _jawab(
            conn, ctx, aksi, id_usulan, now=now, telegram=telegram,
            transport=transport, shadow=shadow, verdict=verdict,
        )
    return diulang


def tick_cycle(
    conn,
    *,
    now,
    telegram,
    transport=None,
    shadow=True,
    db_state=None,
    exchange_state=None,
    verdict=spread.spread_verdict,
    ctx=None,
    fetch=None,
):
    """Satu denyut jantung. Mengembalikan "halted" | "proposed" | "kosong".

    `db_state`/`exchange_state` `None` berarti rekonsiliasi dilewati (dipakai
    tes yang tidak mengujinya); keduanya non-`None` berarti WAJIB diperiksa
    sebelum usulan apa pun.
    """
    ctx = _konteks_kosong() if ctx is None else ctx
    # Sakelar DCA per siklus (Task 15.4): dihitung SEBELUM polling supaya
    # `/ulang` pada siklus ini memakai keputusan bulan yang sama.
    ctx = dataclasses.replace(
        ctx, dca_enabled=dca_window.boleh_usul_dca(conn, now))

    # 1. Rekonsiliasi lebih dulu: buku yang tidak bisa dipercaya tidak boleh
    #    menghasilkan usulan apa pun.
    if db_state is not None and exchange_state is not None:
        selisih = reconcile.mismatches(db_state, exchange_state)
        if selisih:
            for baris in selisih:
                telegram.line(baris)
            shadow_log.record(
                conn,
                types.SimpleNamespace(
                    id=f"rekonsiliasi_halt_{int(now * 1_000_000)}",
                    created_ts=now,
                    kind="HALT",
                    pair=None,
                    amount_idr=None,
                    price_ref=None,
                    rule="rekonsiliasi",
                ),
                "halted",
                "; ".join(selisih),
            )
            return "halted"

    # 2. Izin bursa dibaca ulang tepat sebelum ada keputusan (spec §6.6).
    #    Tanpa transport tidak ada kunci yang bisa dibaca — dan tanpa kunci
    #    tidak ada order yang bisa jalan.
    if transport is not None:
        izin = safeguards.permissions_ok(
            transport.can_trade, transport.can_withdraw, switch_on=not shadow
        )
        if izin is not None:
            telegram.line(izin)
            return "halted"

    # 3. Polling Telegram — wajib, bukan pelengkap (F6).
    diulang = _polling(conn, ctx, now=now, telegram=telegram,
                       transport=transport, shadow=shadow, verdict=verdict,
                       fetch=fetch)

    # 4. Pengingat pasif DCA, maksimal satu pesan per slot jam (T-13.1).
    if dca_window.should_remind(conn, now):
        slot = (time.strftime("%Y-%m-%d", time.localtime(now)),
                time.localtime(now).tm_hour)
        if slot not in _SLOT_PENGINGAT:
            _SLOT_PENGINGAT.add(slot)
            sisa = dca_window.dca_state(conn, now)[1] / 3600.0
            telegram.line(
                "Pengingat: konfirmasi DCA bulan ini belum dijawab. "
                f"Sisa waktu {sisa:.1f} jam — tekan Setuju atau Batal bulan ini."
            )

    # 5. Satu tick engine — DILEWATI kalau `/ulang` sudah mengusulkan pada
    #    siklus ini. Usulan ulang memakai aksi dan `now` yang sama persis
    #    dengan tick ini, jadi `proposal.new_proposal` akan menghasilkan id
    #    kembar dan `shadow_log` menimpanya (atau gagal IntegrityError).
    try:
        usulan = [] if diulang else engine.tick(
            conn, ctx, now=now, telegram=telegram, shadow=shadow,
            verdict=verdict,
        )
    except ValueError:
        # Aksi tanpa provenance = BERHENTI SEGERA, tanpa diulang. Satu-satunya
        # arah aman: nama aturan yang hilang berarti keputusan tidak bisa
        # ditelusuri, dan menebak berarti mempertahankan angka yang salah.
        # Yang TIDAK dijamin "diam": bila ValueError datang di TENGAH daftar
        # aksi, pesan konfirmasi untuk aksi SEBELUMNYA mungkin sudah terkirim
        # ke pengguna — tetapi `usulan` tidak pernah dikembalikan, jadi
        # `_ingat` tidak mendaftarkannya dan tombolnya inert lewat jalur
        # "id tak dikenal" (F46). Konsekuensi jendela DCA: `_catat_dca` tidak
        # jalan, bulan DCA TETAP TERBUKA — percobaan belum dihitung habis dan
        # diusulkan ulang pada siklus sehat berikutnya (selama jendela 24 jam
        # masih berakhir di bulan yang sama). Pesan konfirmasi yatim itu
        # tidak mengunci apa pun dan tidak bisa memicu order.
        return "halted"
    _ingat(usulan)
    # Pencatatan jendela DCA (Task 15.4): buka bila usulan DCA benar-benar
    # terkirim, tutup bulan bila percobaan habis tanpa hasil. Guard
    # `not diulang`: siklus `/ulang` sudah mencatat di `apply_ulang` —
    # dua pencatatan untuk satu keputusan bulan adalah bug, bukan pengaman.
    if ctx.dca_enabled and not diulang:
        _catat_dca(conn, now, usulan)

    # 6. Sakelar mode bayangan.
    apply_shadow_gate(conn, now=now)

    return "proposed" if usulan else "kosong"


def apply_ulang(conn, ctx, *, now, telegram, shadow,
                verdict=spread.spread_verdict):
    """/ulang: usulkan SEKARANG dengan data saat ini juga.

    Hanya pengguna yang memicu (spec §7.1 butir 4) — tidak menunggu denyut
    jantung berikutnya. Angka diambil dari konteks SEKARANG; usulan lama tidak
    pernah dipakai ulang, karena angkanya bisa sudah bergeser.
    """
    usulan = _ingat(engine.tick(
        conn, ctx, now=now, telegram=telegram, shadow=shadow, verdict=verdict,
    ))
    # `/ulang` pada jam usul tetap mencatat jendela (Task 15.4): usulan
    # terkirim lewat sini = jendela resmi dibuka; nol terkirim = percobaan
    # habis. `tick_cycle` tidak ikut mencatat (guard `not diulang`).
    if ctx.dca_enabled:
        _catat_dca(conn, now, usulan)
    return usulan


def _env(kunci):
    """Ambil dari environment atau `.env` — bukan kunci dagang, hanya token."""
    return scheduler._env(kunci)


def main(argv=None, *, conn=None, db_path=None, telegram=None,
         transport_factory=None, ctx=None, fetch=None, jam=None, tidur=None):
    """Jalankan loop. 0 = berhenti wajar, 1 = gagal (konsisten `scheduler.py`).

    `transport_factory` adalah satu-satunya jalan yang MUNGKIN menyentuh
    bursa, dan hanya dipanggil bila sakelar sudah menyala. Tanpa factories
    apa pun, `main()` tidak membangun objek apa pun ke bursa, mencetak satu
    baris berbahasa Indonesia yang jelas, lalu melanjutkan dalam mode bayangan.
    """
    parser = argparse.ArgumentParser(
        prog="main_loop",
        description="Loop utama bot semi-otomatis (mode bayangan secara bawaan).")
    parser.add_argument("--siklus", type=int, default=None,
                        help="Jumlah denyut jantung lalu keluar (bawaan: terus).")
    args = parser.parse_args(argv)

    jam = time.time if jam is None else jam
    tidur = time.sleep if tidur is None else tidur
    conn_milik = False

    try:
        if conn is None:
            conn = portfolio_store.connect(db_path or portfolio_store.DB_FILE)
            portfolio_store.init_db(conn)
            conn_milik = True
        dca_window.ensure_bot_state(conn)
    except Exception as e:
        print(f"Gagal menyiapkan basis data: {e}")
        return 1

    try:
        if telegram is None:
            token = _env("TELEGRAM_TOKEN")
            chat = _env("TELEGRAM_CHAT_ID")
            if not token or not chat:
                print(
                    "Telegram belum diset (TELEGRAM_TOKEN/TELEGRAM_CHAT_ID) — "
                    "bot berhenti: gerbang adalah satu-satunya jalan keluar."
                )
                return 1
            telegram = telegram_gate.Telegram(token, chat)

        reset_state()
        transport = None
        telah_menyala = False

        if ctx is None:
            ctx = _konteks_kosong()

        siklus = 0
        while args.siklus is None or siklus < args.siklus:
            sekarang = jam()
            keadaan = load_state(conn)
            shadow = not keadaan["v1_enabled"]

            if shadow:
                transport = None
                if not telah_menyala:
                    print(
                        "Eksekusi belum dinyalakan: berjalan dalam mode bayangan, "
                        "tidak ada order yang bisa dikirim."
                    )
            elif transport_factory is None:
                transport = None
                if not telah_menyala:
                    print(
                        "Eksekusi belum dinyalakan: tidak ada pabrik transport, "
                        "tidak ada order yang bisa dikirim."
                    )
            else:
                if transport is None:
                    transport = transport_factory()
                telah_menyala = True

            if should_run_habitual(sekarang):
                tick_cycle(conn, now=sekarang, telegram=telegram,
                           transport=transport, shadow=shadow, ctx=ctx,
                           fetch=fetch)
            # Tidur selalu, termasuk di luar jam aktif: hitungan denyut harus tetap
            # berjalan supaya perubahan jam lokal langsung diikuti.
            tidur(HEARTBEAT_S)
            siklus += 1
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"Gagal menjalankan loop: {e}")
        return 1
    finally:
        if conn_milik and conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
