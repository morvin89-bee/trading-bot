"""Log mode bayangan: catat setiap usulan dan hasilnya untuk review harian.

Semua fungsi murni lokal (tanpa jaringan) agar mudah diuji.
"""
import time
import sqlite3


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
    """Buat tabel proposals jika belum ada."""
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
                note TEXT
            )
            """
        )


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
            id, ts, day, kind, pair, amount_idr, price_ref, rule, outcome, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    conn.commit()


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