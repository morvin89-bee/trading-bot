"""Tes mode bayangan — tidak pernah membuka jaringan."""
import os
import tempfile
import proposal
import rules
import shadow_log
import sqlite3


def _fresh_db(tmp_path):
    """Buat koneksi ke basis data baru dan kosong."""
    db_path = os.path.join(tmp_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _act(kind="BUY", pair="btc_idr", amount=275000, price=1000.0):
    return rules.Action(kind, pair, amount, amount / price, price, "alasan uji", True)


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM proposals ORDER BY id")]


def test_records_every_proposal_with_its_rule(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="Take-profit +50%", reserve_after=0,
                              window_s=90, now=0.0)
    shadow_log.record(conn, p, "proposed")
    assert [r["rule"] for r in _rows(conn)] == ["Take-profit +50%"]


def test_summary_lists_each_outcome_in_indonesian(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="DCA bulanan", reserve_after=0,
                              window_s=86400, now=0.0)
    shadow_log.record(conn, p, "expired")
    lines = shadow_log.daily_summary(conn, "1970-01-01")
    assert len(lines) == 1 and "kedaluarsa" in lines[0]


def test_rejected_count_is_the_shadow_reset_signal(tmp_path):
    conn = _fresh_db(tmp_path)
    for i, outcome in enumerate(("gate_rejected", "spread_rejected")):
        p = proposal.new_proposal(_act(), rule="r", reserve_after=0,
                                  window_s=90, now=float(i))
        shadow_log.record(conn, p, outcome)
    assert shadow_log.rejected_count(conn, "1970-01-01") == 2