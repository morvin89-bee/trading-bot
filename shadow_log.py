"""Log mode bayangan: catat setiap usulan dan hasilnya untuk review harian.

Semua fungsi murni lokal (tanpa jaringan) agar mudah diuji.
"""
import time
import sqlite3

import proposal


# Hasil yang mungkin terjadi pada suatu usulan
outcomes = (
    "proposed",
    "undelivered",
    "requoted",
    "setuju",
    "tunda",
    "batal",
    "expired",
    "spread_rejected",
    "gate_rejected",
    "dca_missed",
    "halted",
    "executed",
)


def _ensure_table(conn):
    """Buat tabel proposals jika belum ada, lalu lengkapi kolom barunya.

    Kolom pemulihan menyimpan angka yang dibutuhkan untuk memulihkan usulan
    yang masih menunggu jawaban setelah proses restart. Tanpa kolom itu
    usulan yang sudah terkirim ke Telegram menjadi inert saat bot hidup lagi,
    dan jendela DCA 24 jam berakhir dengan catatan `dca_missed` palsu.
    `level`/`basis` menyimpan ambang take-profit agar memori ambang tetap
    benar walau usulan dieksekusi setelah restart.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proposals'")
    if not cursor.fetchone():
        cursor.execute(
            """
            CREATE TABLE proposals (
                id TEXT PRIMARY KEY,
                ts REAL,
                day TEXT,
                kind TEXT,
                pair TEXT,
                amount_idr REAL,
                price_ref REAL,
                rule TEXT,
                outcome TEXT,
                note TEXT,
                qty REAL,
                reserve_after REAL,
                window_s INTEGER,
                level REAL,
                basis REAL
            )
            """
        )
        return
    # Tabel lama (dibuat sebelum P1.3) belum punya kolom pemulihan. Cek
    # lewat PRAGMA supaya migrasi idempoten — ALTER kedua akan gagal keras.
    kolom = [baris[1] for baris in conn.execute("PRAGMA table_info(proposals)")]
    for nama, tipe in (("qty", "REAL"), ("reserve_after", "REAL"),
                       ("window_s", "INTEGER"), ("level", "REAL"),
                       ("basis", "REAL")):
        if nama not in kolom:
            conn.execute(f"ALTER TABLE proposals ADD COLUMN {nama} {tipe}")


def record(conn, p, outcome, note=""):
    """Catat satu usulan dengan hasilnya.

    Argumen:
        conn: Koneksi basis data SQLite
        p: Objek proposal.Proposal yang dilampirkan
        outcome: Salah satu dari:
            "proposed", "setuju", "tunda", "batal", "expired",
            "spread_rejected", "gate_rejected", "dca_missed", "halted", "executed"
        note: Catatan tambahan (opsional)

    Catat baris ke tabel proposals dengan kolom:
        id, ts, day, kind, pair, amount_idr, price_ref, rule, outcome, note

    day dihitung menggunakan time.localtime (lihat Global Constraints).
    """
    _ensure_table(conn)
    
    # Hitung day menggunakan time.localtime sesuai Global Constraints
    day = time.strftime("%Y-%m-%d", time.localtime(p.created_ts))
    
    conn.execute(
        """
        INSERT INTO proposals (
            id, ts, day, kind, pair, amount_idr, price_ref, rule, outcome,
            note, qty, reserve_after, window_s, level, basis
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.id,
            p.created_ts,
            day,
            p.kind,
            p.pair,
            p.amount_idr,
            p.price_ref,
            p.rule,
            outcome,
            note,
            getattr(p, "qty", None),
            getattr(p, "reserve_after", None),
            getattr(p, "window_s", None),
            getattr(p, "level", None),
            getattr(p, "basis", None),
        ),
    )
    conn.commit()


def outcome(conn, id):
    """Outcome tercatat untuk `id`, atau None bila belum ada/tabel belum dibuat.

    Tabel `proposals` milik modul ini; pemanggil lain tidak perlu mengetahui
    bentuk skemanya. `OperationalError` berarti tabel belum pernah dibuat —
    belum ada catatan apa pun, bukan kegagalan.
    """
    try:
        baris = conn.execute(
            "SELECT outcome FROM proposals WHERE id = ?", (id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return baris[0] if baris is not None else None


def void_pending(conn, rule, now, note=""):
    """Tandai semua usulan `rule` yang jendelanya masih terbuka sebagai "batal".

    Dipakai tombol Batal DCA: satu tekanan menutup SELURUH usulan DCA bulan
    itu, bukan hanya id yang kebetulan ditekan. Usulan yang jendelanya sudah
    lewat tidak disentuh — statusnya bukan lagi "menunggu".
    """
    _ensure_table(conn)
    conn.execute(
        "UPDATE proposals SET outcome = 'batal', note = ? "
        "WHERE outcome = 'proposed' AND rule = ? AND ts IS NOT NULL "
        "AND window_s IS NOT NULL AND ? < ts + window_s",
        (note, rule, now),
    )
    conn.commit()


def pending(conn, now):
    """Usulan berstatus "proposed" yang jendelanya masih terbuka pada `now`.

    Dipakai `main_loop` untuk memulihkan `_USULAN_MENUNGGU` setelah restart:
    tombol yang sudah dikirim ke pengguna harus tetap bisa dijawab. Usulan
    yang jendelanya sudah lewat TIDAK dimuat — konfirmasi hangus, dan
    `proposal.is_expired` akan menolaknya pula.

    Baris lama tanpa angka pemulihan (`window_s`/`qty` NULL) dilewati:
    merekonstruksi usulan dari angka yang tidak lengkap berarti menebak.
    """
    _ensure_table(conn)
    baris = conn.execute(
        """
        SELECT id, kind, pair, amount_idr, qty, price_ref, rule,
               reserve_after, window_s, ts, level, basis
        FROM proposals
        WHERE outcome = 'proposed' AND ts IS NOT NULL
          AND window_s IS NOT NULL AND qty IS NOT NULL
          AND reserve_after IS NOT NULL AND ? < ts + window_s
        """,
        (now,),
    ).fetchall()
    return [
        proposal.Proposal(
            id=r["id"], kind=r["kind"], pair=r["pair"],
            amount_idr=r["amount_idr"], qty=r["qty"],
            price_ref=r["price_ref"], rule=r["rule"],
            reserve_after=r["reserve_after"], window_s=r["window_s"],
            created_ts=r["ts"], status="proposed",
            level=r["level"], basis=r["basis"],
        )
        for r in baris
    ]


def perbarui_harga(conn, p, note=""):
    """Perbarui harga/qty usulan yang di-re-quote; id dan outcome tetap.

    Dipakai re-quote DCA: angka di pesan baru harus sama dengan angka yang
    tersimpan, supaya tombol yang sama setelah restart tidak memakai harga
    lama. `id`, `ts`, dan `outcome` tidak disentuh.
    """
    _ensure_table(conn)
    conn.execute(
        "UPDATE proposals SET price_ref = ?, qty = ?, note = ? WHERE id = ?",
        (p.price_ref, p.qty, note, p.id),
    )
    conn.commit()


def _buat_tabel_take_profit(conn):
    """Buat tabel memori ambang take-profit bentuk baru (idempoten)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS take_profit_state (
            mode TEXT NOT NULL,
            pair TEXT NOT NULL,
            level REAL NOT NULL,
            qty_target REAL NOT NULL DEFAULT 0,
            qty_terjual REAL NOT NULL DEFAULT 0,
            last_day TEXT,
            updated_ts REAL,
            PRIMARY KEY (mode, pair, level)
        )
        """
    )


def _ensure_take_profit_state(conn):
    """Buat/migrasi tabel memori ambang take-profit (idempoten).

    Tabel lama dikunci pada `(pair, avg_cost)`; DCA yang mengubah avg_cost
    membuat ambang yang sudah dieksekusi berlaku lagi. Bentuk baru tidak
    menyimpan avg_cost sama sekali: reset hanya terjadi saat posisi ditutup.
    Baris lama disalin ke KEDUA mode dengan target 0 (sudah selesai) supaya
    pembaruan tidak membuka penjualan dobel.
    """
    baris = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='take_profit_state'").fetchone()
    if baris is None:
        _buat_tabel_take_profit(conn)
        conn.commit()
        return
    kolom = [r[1] for r in conn.execute("PRAGMA table_info(take_profit_state)")]
    if "mode" in kolom:
        return
    conn.execute(
        "ALTER TABLE take_profit_state RENAME TO take_profit_state_lama")
    _buat_tabel_take_profit(conn)
    for mode in ("shadow", "live"):
        conn.execute(
            "INSERT OR IGNORE INTO take_profit_state "
            "(mode, pair, level, qty_target, qty_terjual, last_day, updated_ts) "
            "SELECT ?, pair, level, 0.0, 0.0, NULL, updated_ts "
            "FROM take_profit_state_lama",
            (mode,),
        )
    conn.execute("DROP TABLE take_profit_state_lama")
    conn.commit()


def take_profit_state(conn, pairs=None, mode="shadow"):
    """`{(pair, level): (qty_target, qty_terjual, last_day)}` per mode.

    `pairs` (bila diisi) adalah pair yang masih dipegang; baris untuk pair
    yang sudah tidak dipegang DIHAPUS di SEMUA mode — posisi tertutup =
    siklus baru. `avg_cost` tidak lagi menjadi kunci: pembelian DCA tidak
    boleh membuka kembali ambang yang sudah dieksekusi.
    """
    _ensure_take_profit_state(conn)
    baris = conn.execute(
        "SELECT pair, level, qty_target, qty_terjual, last_day "
        "FROM take_profit_state WHERE mode = ?", (mode,)).fetchall()
    state = {(pair, level): (target, terjual, last_day)
             for pair, level, target, terjual, last_day in baris}
    if pairs is not None:
        semua = {r[0] for r in conn.execute(
            "SELECT DISTINCT pair FROM take_profit_state")}
        basi = semua - set(pairs)
        if basi:
            conn.executemany(
                "DELETE FROM take_profit_state WHERE pair = ?",
                [(pair,) for pair in basi])
            conn.commit()
            state = {kunci: nilai for kunci, nilai in state.items()
                     if kunci[0] not in basi}
    return state


def catat_take_profit(conn, pair, level, *, qty_target, qty, ts,
                      mode="shadow", hari=None):
    """Catat satu tranche take-profit yang benar-benar dieksekusi.

    `qty_target` adalah total target level itu; `qty` adalah bagian yang
    terjual pada tranche ini. Baris yang sudah ada menambah `qty_terjual`
    (tidak pernah melebihi target) dan memperbarui `last_day`; target TIDAK
    pernah diganti oleh pembelian DCA berikutnya.
    """
    _ensure_take_profit_state(conn)
    baris = conn.execute(
        "SELECT qty_target, qty_terjual FROM take_profit_state "
        "WHERE mode = ? AND pair = ? AND level = ?",
        (mode, pair, level)).fetchone()
    if baris is None:
        target = float(qty_target)
        terjual = min(float(qty), target) if target > 0 else float(qty)
        conn.execute(
            "INSERT INTO take_profit_state "
            "(mode, pair, level, qty_target, qty_terjual, last_day, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mode, pair, level, target, terjual, hari, ts))
    else:
        target = float(baris[0])
        terjual = float(baris[1]) + float(qty)
        if target > 0:
            terjual = min(terjual, target)
        conn.execute(
            "UPDATE take_profit_state SET qty_terjual = ?, last_day = ?, "
            "updated_ts = ? WHERE mode = ? AND pair = ? AND level = ?",
            (terjual, hari, ts, mode, pair, level))
    conn.commit()


def hapus_take_profit(conn, pair, level, *, qty, mode="shadow"):
    """Batalkan catatan satu tranche (status akhir rejected/cancelled).

    `qty` dikurangkan dari `qty_terjual`; baris dihapus bila tidak ada sisa.
    Ini mengembalikan state ke sebelum tranche yang ternyata tidak jadi.
    """
    _ensure_take_profit_state(conn)
    baris = conn.execute(
        "SELECT qty_terjual FROM take_profit_state "
        "WHERE mode = ? AND pair = ? AND level = ?",
        (mode, pair, level)).fetchone()
    if baris is None:
        return
    sisa = float(baris[0]) - float(qty)
    if sisa <= 0:
        conn.execute(
            "DELETE FROM take_profit_state "
            "WHERE mode = ? AND pair = ? AND level = ?", (mode, pair, level))
    else:
        conn.execute(
            "UPDATE take_profit_state SET qty_terjual = ? "
            "WHERE mode = ? AND pair = ? AND level = ?",
            (sisa, mode, pair, level))
    conn.commit()


def hapus_take_profit_mode(conn, mode):
    """Hapus seluruh memori TP satu mode; dipakai `aktifkan_live`."""
    _ensure_take_profit_state(conn)
    conn.execute("DELETE FROM take_profit_state WHERE mode = ?", (mode,))
    conn.commit()


def dca_bulan_ini(conn, rule, bulan):
    """Pair DCA yang sudah terkirim dan jumlah percobaan gagal bulan ini.

    `bulan` berformat "YYYY-MM" waktu lokal. "Terkirim" berarti konfirmasi
    sudah sampai ke pengguna (`proposed`/`setuju`/`executed`); `undelivered`
    adalah percobaan yang gagal dikirim dan boleh diulang. Baris audit
    `dca_missed` (pair NULL) diabaikan.
    """
    _ensure_table(conn)
    baris = conn.execute(
        "SELECT pair, outcome, COUNT(*) FROM proposals "
        "WHERE rule = ? AND day LIKE ? GROUP BY pair, outcome",
        (rule, f"{bulan}-%"),
    ).fetchall()
    terkirim = set()
    gagal = {}
    for pair, outcome, jumlah in baris:
        if pair is None:
            continue
        if outcome in ("proposed", "setuju", "executed"):
            terkirim.add(pair)
        elif outcome == "undelivered":
            gagal[pair] = gagal.get(pair, 0) + jumlah
    return terkirim, gagal


def daily_summary(conn, day):
    """Kembalikan rangkuman harian dalam Bahasa Indonesia untuk review.

    Argumen:
        conn: Koneksi basis data SQLite
        day: String dalam format "YYYY-MM-DD"

    Kembalikan:
        Daftar string Bahasa Indonesia, satu per tipe hasil yang terjadi hari tersebut.
        Setiap string berisi jumlah dan keterangan hasil tersebut.
    """
    # Mapping hasil ke Bahasa Indonesia
    outcome_to_id = {
        "proposed": "diusulkan",
        "undelivered": "gagal dikirim",
        "requoted": "di-re-quote",
        "setuju": "disetujui",
        "tunda": "ditunda",
        "batal": "dibatalkan",
        "expired": "kedaluarsa",
        "spread_rejected": "ditolak karena spread",
        "gate_rejected": "ditolak oleh gerbang",
        "dca_missed": "DCA dilewati",
        "halted": "ditangguhkan",
        "executed": "dieksekusi",
    }
    
    # Hitung jumlah setiap hasil untuk hari yang diberikan
    cursor = conn.execute(
        """
        SELECT outcome, COUNT(*) as count
        FROM proposals
        WHERE day = ?
        GROUP BY outcome
        """,
        (day,),
    )
    
    lines = []
    for row in cursor:
        outcome = row["outcome"]
        count = row["count"]
        if outcome in outcome_to_id:
            id_text = outcome_to_id[outcome]
            lines.append(f"{count} usulan {id_text}")
    
    return lines


def rejected_count(conn, day):
    """Hitung jumlah usulan yang ditolak (gate_rejected + spread_rejected) untuk hari tertentu.

    Angka ini adalah sinyal untuk mereset shadow_day ke 0 (lihat spec Â§9).

    Argumen:
        conn: Koneksi basis data SQLite
        day: String dalam format "YYYY-MM-DD"

    Kembalikan:
        Integer jumlah usulan dengan outcome gate_rejected atau spread_rejected.
    """
    cursor = conn.execute(
        """
        SELECT COUNT(*) as count
        FROM proposals
        WHERE day = ? AND outcome IN ('gate_rejected', 'spread_rejected')
        """,
        (day,),
    )
    row = cursor.fetchone()
    return row["count"] if row else 0