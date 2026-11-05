"""Log mode bayangan: catat setiap usulan dan hasilnya untuk review harian.

Semua fungsi murni lokal (tanpa jaringan) agar mudah diuji.
"""
import time
import sqlite3

import proposal


# Hasil yang mungkin terjadi pada suatu usulan
outcomes = (
    "proposed",
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

    Tiga kolom terakhir menyimpan angka yang dibutuhkan untuk memulihkan
    usulan yang masih menunggu jawaban setelah proses restart. Tanpa kolom
    itu usulan yang sudah terkirim ke Telegram menjadi inert saat bot hidup
    lagi, dan jendela DCA 24 jam berakhir dengan catatan `dca_missed` palsu.
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
                window_s INTEGER
            )
            """
        )
        return
    # Tabel lama (dibuat sebelum P1.3) belum punya kolom pemulihan. Cek
    # lewat PRAGMA supaya migrasi idempoten — ALTER kedua akan gagal keras.
    kolom = [baris[1] for baris in conn.execute("PRAGMA table_info(proposals)")]
    for nama, tipe in (("qty", "REAL"), ("reserve_after", "REAL"),
                       ("window_s", "INTEGER")):
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
            note, qty, reserve_after, window_s
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
               reserve_after, window_s, ts
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
        )
        for r in baris
    ]


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