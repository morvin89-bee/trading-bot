"""Tes jendela DCA 24 jam, pengingat pasif, dan DND — tidak pernah membuka jaringan."""
import calendar
import inspect
import os
import sqlite3
import time

import pytest

import dca_window


def _fresh_db(tmp_path):
    """Buat koneksi ke basis data baru dan kosong (pola tests/test_shadow_log.py)."""
    db_path = os.path.join(tmp_path, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(day=1, hour=0, minute=0):
    """Timestamp epoch dari WAKTU LOKAL — seluruh rencana memakai localtime."""
    kini = time.localtime()
    struktur = time.struct_time(
        (kini.tm_year, kini.tm_mon, day, hour, minute, 0, 0, 0, -1)
    )
    return time.mktime(struktur)


def _ts_berikutnya(hour=9):
    """Hari pertama bulan berikutnya, waktu lokal."""
    kini = time.localtime()
    tahun, bulan = kini.tm_year, kini.tm_mon + 1
    if bulan > 12:
        bulan, tahun = 1, tahun + 1
    return time.mktime(time.struct_time((tahun, bulan, 1, hour, 0, 0, 0, 0, -1)))


def _hari_terakhir_bulan_ini():
    """Hari terakhir bulan berjalan — untuk membangun jendela lintas batas bulan."""
    kini = time.localtime()
    return calendar.monthrange(kini.tm_year, kini.tm_mon)[1]


def _ada_tabel_proposals(conn):
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='proposals'"
        ).fetchone()[0]
        == 1
    )


def _jumlah_missed(conn):
    """Hitung catatan dca_missed; 0 bila tabel proposals belum pernah dibuat."""
    if not _ada_tabel_proposals(conn):
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE outcome = 'dca_missed'"
    ).fetchone()[0]


def test_constants_match_contract():
    assert dca_window.DCA_SEND_HOUR == 9
    assert dca_window.DCA_WINDOW_H == 24
    assert dca_window.DND == (23, 7)
    # Minor 5 — dipatok eksplisit, bukan lewat tes lain secara tak langsung
    assert dca_window.STATUS_DCA == ("awaiting", "confirmed", "missed", "batal")


def test_dnd_window_covers_23_to_07():
    assert dca_window.in_dnd(_ts(hour=23, minute=0)) is True
    assert dca_window.in_dnd(_ts(hour=2, minute=0)) is True
    assert dca_window.in_dnd(_ts(hour=7, minute=0)) is False
    assert dca_window.in_dnd(_ts(hour=12, minute=0)) is False
    # batas tepat: 22.00 masih di luar, 06.00 masih di dalam
    assert dca_window.in_dnd(_ts(hour=22, minute=0)) is False
    assert dca_window.in_dnd(_ts(hour=6, minute=0)) is True


def test_reminder_only_at_15_and_21(tmp_path):
    # Review Focus #5 — pengingat 03.00 jatuh dalam DND, tidak boleh dikirim
    assert dca_window.REMINDER_HOURS == (15, 21)
    assert dca_window.in_dnd(_ts(hour=3, minute=0)) is True
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    assert dca_window.should_remind(conn, _ts(hour=3, minute=0)) is False


def test_missed_after_24_hours_records_dca_missed(tmp_path):
    # Nama tes ini diperbaiki: brief menamainya "..._writes_nothing" padahal
    # Step 3 mewajibkan dca_window MENULIS shadow_log "dca_missed" sendiri (F8).
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    state, sisa = dca_window.dca_state(conn, _ts(day=2, hour=9, minute=1))
    assert state == "missed"
    assert sisa == 0.0
    assert _ada_tabel_proposals(conn) is True
    assert _jumlah_missed(conn) == 1


def test_dca_missed_recorded_exactly_once(tmp_path):
    # Syarat Step 3 yang tidak dipatok brief: dicatat SEKALI saat transisi,
    # bukan setiap siklus.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.dca_state(conn, _ts(day=2, hour=9, minute=1))
    dca_window.dca_state(conn, _ts(day=2, hour=12, minute=0))  # siklus berikutnya
    assert _jumlah_missed(conn) == 1


def test_new_schedule_appears_next_month_only(tmp_path):
    conn = _fresh_db(tmp_path)
    # jendela dulu: sejak T-13.6 mark_missed bergerbang (butuh jendela + awaiting)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_missed(conn, _ts(hour=10, minute=0))
    # hari berikutnya di bulan yang sama: tidak ada jadwal baru
    assert dca_window.dca_state(conn, _ts(day=2, hour=9, minute=0))[0] == "missed"
    # bulan berikutnya barulah siklus baru muncul ("none")
    assert dca_window.dca_state(conn, _ts_berikutnya(hour=9))[0] == "none"


def test_states_follow_the_window(tmp_path):
    conn = _fresh_db(tmp_path)
    assert dca_window.dca_state(conn, _ts(hour=9, minute=0)) == ("none", 0.0)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    state, sisa = dca_window.dca_state(conn, _ts(hour=9, minute=1))
    assert state == "awaiting"
    assert sisa == 86340.0  # 86.400 detik − 60 detik (dihitung, bukan dikutip)
    # ^ literal DST-rapuh: benar di mesin ini (UTC+7, tanpa DST); mesin ber-DST bisa beda
    dca_window.mark_confirmed(conn, _ts(hour=10, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=11, minute=0)) == ("confirmed", 0.0)


def test_should_remind_only_at_15_and_21_while_awaiting(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    assert dca_window.should_remind(conn, _ts(hour=15, minute=0)) is True
    assert dca_window.should_remind(conn, _ts(hour=21, minute=0)) is True
    assert dca_window.should_remind(conn, _ts(hour=12, minute=0)) is False
    assert dca_window.should_remind(conn, _ts(hour=16, minute=0)) is False
    # 23.00 bukan jam pengingat (dan berada di DND)
    assert dca_window.should_remind(conn, _ts(hour=23, minute=0)) is False
    dca_window.mark_confirmed(conn, _ts(hour=14, minute=0))
    assert dca_window.should_remind(conn, _ts(hour=15, minute=0)) is False
    kosong = _fresh_db(tmp_path)
    assert dca_window.should_remind(kosong, _ts(hour=15, minute=0)) is False


def test_should_remind_is_passive_and_sends_nothing(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    sebelum = conn.execute(
        "SELECT last_dca_date, dca_sent_ts, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert dca_window.should_remind(conn, _ts(hour=15, minute=0)) is True
    sesudah = conn.execute(
        "SELECT last_dca_date, dca_sent_ts, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert tuple(sebelum) == tuple(sesudah)
    assert sesudah[2] == "awaiting"  # "Tunda" tidak pernah berubah jadi "Setuju"
    assert _ada_tabel_proposals(conn) is False  # nol tulisan sama sekali
    sumber = inspect.getsource(dca_window)
    assert "telegram" not in sumber  # pengirimannya urusan pemanggil (Task 14)
    assert "urllib" not in sumber and "requests" not in sumber
    assert "rebalance_actions" not in sumber
    assert "drawdown" not in sumber.lower()


def test_ensure_bot_state_idempotent(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.ensure_bot_state(conn)
    dca_window.ensure_bot_state(conn)  # panggilan kedua: tidak error, tidak ganda
    baris = conn.execute(
        "SELECT id, last_dca_date, v1_enabled, shadow_day FROM bot_state"
    ).fetchall()
    assert len(baris) == 1
    assert baris[0][0] == 1
    assert baris[0][1] is None
    assert baris[0][2] == 0
    assert baris[0][3] == 0
    # mempertahankan data yang sudah tersimpan
    kirim = _ts(hour=9, minute=0)
    dca_window.mark_pending(conn, kirim)
    dca_window.ensure_bot_state(conn)
    sesudah = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert sesudah[0] == time.strftime("%Y-%m", time.localtime(kirim))
    assert sesudah[1] == "awaiting"


def test_mark_missed_records_once_even_if_called_twice(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))  # gerbang T-13.6: butuh jendela
    dca_window.mark_missed(conn, _ts(hour=10, minute=0))
    # panggilan ganda pada waktu berbeda di bulan yang sama — tidak boleh menggandakan
    dca_window.mark_missed(conn, _ts(hour=11, minute=0))
    assert _jumlah_missed(conn) == 1
    assert dca_window.dca_state(conn, _ts(day=2, hour=10, minute=0))[0] == "missed"


def test_missed_marker_carries_no_fabricated_numbers(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))  # gerbang T-13.6: butuh jendela
    ts = _ts(hour=10, minute=0)
    dca_window.mark_missed(conn, ts)
    baris = conn.execute(
        "SELECT id, ts, day, kind, pair, amount_idr, price_ref, rule, outcome "
        "FROM proposals"
    ).fetchone()
    bulan = time.strftime("%Y-%m", time.localtime(ts))
    assert baris[0] == f"dca_missed_{bulan}"
    assert baris[1] == ts
    assert baris[2] == time.strftime("%Y-%m-%d", time.localtime(ts))  # localtime
    assert baris[3] == "DCA"
    assert baris[4] is None  # pair, nominal, dan harga tidak dikarang
    assert baris[5] is None
    assert baris[6] is None
    assert baris[7] == "DCA bulanan"
    assert baris[8] == "dca_missed"


def test_late_confirmation_does_not_resurrect_missed(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))  # gerbang T-13.6: butuh jendela
    dca_window.mark_missed(conn, _ts(hour=10, minute=0))
    dca_window.mark_confirmed(conn, _ts(hour=12, minute=0))  # jawaban telat
    assert dca_window.dca_state(conn, _ts(hour=13, minute=0))[0] == "missed"
    assert _jumlah_missed(conn) == 1


def test_window_boundary_is_end_exclusive(tmp_path):
    # Batas akhir eksklusif (sejajar putusan DND: mulai inklusif, akhir eksklusif):
    # 1 detik sebelum tepat 24 jam masih awaiting, tepat 24 jam sudah missed.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    assert dca_window.dca_state(conn, _ts(day=2, hour=8, minute=59)) == ("awaiting", 60.0)  # 60.0: literal DST-rapuh, benar di UTC+7 tanpa DST
    assert dca_window.dca_state(conn, _ts(day=2, hour=9, minute=0)) == ("missed", 0.0)
    assert _jumlah_missed(conn) == 1


def test_should_remind_docstring_states_one_message_per_slot():
    # T-13.1 — should_remind True sepanjang satu jam; dedupe ada di pemanggil
    # (rencana L1189 memanggilnya tiap tick, HEARTBEAT_S = 300). Kalimat ini yang
    # mewariskan kewajiban "maksimal satu pesan per slot" ke Task 14.
    doc = inspect.getdoc(dca_window.should_remind) or ""
    assert "maksimal satu pesan per slot" in doc


def test_crashed_between_audit_and_status_recovers(tmp_path):
    # T-13.2 — sisa kecelakaan: commit baris audit berhasil, commit status mati
    # di tengah (di sini disimulasikan dengan menyetel ulang status ke awaiting).
    # Pemanggilan berikutnya wajib memulihkan diri, bukan mati IntegrityError
    # selamanya sampai bulan berganti.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_missed(conn, _ts(day=2, hour=9, minute=1))
    conn.execute("UPDATE bot_state SET dca_status = 'awaiting' WHERE id = 1")
    conn.commit()
    state, _ = dca_window.dca_state(conn, _ts(day=2, hour=12, minute=0))
    assert state == "missed"          # pulih sendiri, bukan meledak
    assert _jumlah_missed(conn) == 1  # tetap tepat satu catatan


def test_confirm_without_pending_window_is_noop(tmp_path):
    # T-13.3 — tombol Setuju dari keyboard basi yang mendarat SEBELUM pengiriman
    # tidak boleh mengarang bulan "confirmed" (bulan DCA hilang diam-diam).
    conn = _fresh_db(tmp_path)
    dca_window.mark_confirmed(conn, _ts(hour=9, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=10, minute=0)) == ("none", 0.0)
    baris = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] is None and baris[1] is None  # keadaan tidak tersentuh


def test_confirm_during_pending_window_works(tmp_path):
    # sisi kedua T-13.3: pada jendela yang sah, konfirmasi tetap berfungsi
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_confirmed(conn, _ts(hour=10, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=11, minute=0)) == ("confirmed", 0.0)
    assert dca_window.should_remind(conn, _ts(hour=15, minute=0)) is False


def test_confirm_in_different_month_than_pending_is_ignored(tmp_path):
    # T-13.3 — keputusan sadar: jawaban silang-bulan diabaikan, jendela aslinya
    # tidak boleh tertimpa, dan bulan penerima jawaban tidak dikarang "confirmed".
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts_berikutnya(hour=9))
    dca_window.mark_confirmed(conn, _ts(hour=10, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=11, minute=0)) == ("none", 0.0)
    assert dca_window.dca_state(conn, _ts_berikutnya(hour=12))[0] == "awaiting"


def test_unknown_status_fails_loud_not_open(tmp_path):
    # T-13.4 — penulisan langsung (terlarang) menyisipkan nilai tak dikenal:
    # wajib gagal keras, bukan diam-diam jatuh ke awaiting lalu menulis catatan
    # dca_missed palsu untuk DCA yang terjawab.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    conn.execute("UPDATE bot_state SET dca_status = 'rancu' WHERE id = 1")
    conn.commit()
    with pytest.raises(ValueError, match="dca_status"):
        dca_window.dca_state(conn, _ts(hour=10, minute=0))
    assert _jumlah_missed(conn) == 0  # tidak ada catatan palsu


def test_null_sent_ts_fails_loud_with_value_error(tmp_path):
    # Minor 2 — data rusak harus menjelaskan dirinya sendiri, bukan TypeError
    # "None + int" yang buram
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    conn.execute("UPDATE bot_state SET dca_sent_ts = NULL WHERE id = 1")
    conn.commit()
    with pytest.raises(ValueError, match="dca_sent_ts"):
        dca_window.dca_state(conn, _ts(hour=10, minute=0))


def test_mark_batal_writes_no_record_and_yields_none(tmp_path):
    # Putusan "mark_batal dikirim SEKARANG" — tombol "Batal bulan ini" (spec L205):
    # batal bukan penolakan (gate_rejected/spread_rejected) dan bukan kelalaian,
    # jadi nol baris shadow_log — tidak ada kejadian yang perlu dicatat.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_batal(conn, _ts(hour=10, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=11, minute=0)) == ("none", 0.0)
    assert _ada_tabel_proposals(conn) is False  # nol tulisan sama sekali
    assert _jumlah_missed(conn) == 0
    assert dca_window.should_remind(conn, _ts(hour=15, minute=0)) is False


def test_reminder_at_03_blocked_by_dnd_not_by_schedule(tmp_path):
    # Minor 1 — mendiskriminasikan cabang in_dnd di dalam should_remind:
    # dengan set jam pengingat dipaksa jadi (3,), 03.00 tetap tidak boleh True.
    # Minor 6 — jam 03.00 diambil HARI KEDUA (di dalam jendela, sesudah pengiriman
    # 09.00 hari pertama) supaya waktunya berjalan maju, bukan mundur.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    asli = dca_window.REMINDER_HOURS
    try:
        dca_window.REMINDER_HOURS = (3,)
        assert dca_window.should_remind(conn, _ts(day=2, hour=3, minute=0)) is False
    finally:
        dca_window.REMINDER_HOURS = asli


def test_batal_after_confirmation_is_refused(tmp_path):
    # T-13.5 sisi "sudah selesai" (1a) — tombol Batal basi/ganda setelah persetujuan
    # tidak boleh menghapus keputusan yang sudah tercatat (penghapusan tanpa jejak).
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_confirmed(conn, _ts(hour=10, minute=0))
    dca_window.mark_batal(conn, _ts(hour=11, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=12, minute=0)) == ("confirmed", 0.0)
    assert _ada_tabel_proposals(conn) is False  # penolakan tetap nol tulisan


def test_batal_after_missed_is_refused(tmp_path):
    # T-13.5 sisi "sudah selesai" (1b) — "missed" juga sudah selesai: tidak boleh
    # berubah jadi "none" tanpa jejak, dan catatannya tetap satu.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_missed(conn, _ts(day=2, hour=9, minute=1))
    dca_window.mark_batal(conn, _ts(day=2, hour=10, minute=0))
    assert dca_window.dca_state(conn, _ts(day=2, hour=11, minute=0)) == ("missed", 0.0)
    assert _jumlah_missed(conn) == 1


def test_batal_from_other_month_does_not_move_month(tmp_path):
    # T-13.5 sisi "bulan lain" (2) — tombol yang mendarat di bulan berbeda dari
    # jendela yang dipegang baris tidak boleh menggeser last_dca_date.
    conn = _fresh_db(tmp_path)
    jendela = _ts_berikutnya(hour=9)
    dca_window.mark_pending(conn, jendela)
    dca_window.mark_batal(conn, _ts(hour=10, minute=0))
    baris = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] == time.strftime("%Y-%m", time.localtime(jendela))
    assert baris[1] == "awaiting"
    assert _ada_tabel_proposals(conn) is False


def test_mark_missed_without_window_is_refused(tmp_path):
    # T-13.6 sisi "tidak ada jendela" — tanpa jendela (last_dca_date NULL) tidak
    # boleh memalsukan bulan dan tidak boleh menulis catatan.
    conn = _fresh_db(tmp_path)
    dca_window.mark_missed(conn, _ts(hour=10, minute=0))
    baris = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] is None and baris[1] is None  # bulan tidak dikarang
    assert _jumlah_missed(conn) == 0
    # baris rusak: status awaiting tapi bulan NULL (simulasi lewat UPDATE) —
    # "tidak ada jendela" menang, tidak ada klaim bulan
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    conn.execute("UPDATE bot_state SET last_dca_date = NULL WHERE id = 1")
    conn.commit()
    dca_window.mark_missed(conn, _ts(hour=11, minute=0))
    baris = conn.execute(
        "SELECT last_dca_date, dca_status FROM bot_state WHERE id = 1"
    ).fetchone()
    assert baris[0] is None
    assert _jumlah_missed(conn) == 0


def test_mark_missed_does_not_overwrite_confirmed(tmp_path):
    # T-13.6 sisi status (2a) — persetujuan tidak boleh hilang diam-diam bila
    # mark_missed dipanggil langsung pada status confirmed.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_confirmed(conn, _ts(hour=10, minute=0))
    dca_window.mark_missed(conn, _ts(hour=11, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=12, minute=0)) == ("confirmed", 0.0)
    assert _ada_tabel_proposals(conn) is False


def test_mark_missed_does_not_overwrite_batal(tmp_path):
    # T-13.6 sisi status (2b) — bulan yang dibatalkan tidak boleh mendapat
    # catatan dca_missed palsu ("tidak dibalas dalam 24 jam") dari jalur langsung.
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    dca_window.mark_batal(conn, _ts(hour=10, minute=0))
    dca_window.mark_missed(conn, _ts(hour=11, minute=0))
    assert dca_window.dca_state(conn, _ts(hour=12, minute=0)) == ("none", 0.0)
    assert _ada_tabel_proposals(conn) is False
    assert _jumlah_missed(conn) == 0


def test_mark_missed_accepts_window_crossing_month_boundary(tmp_path):
    # T-13.6 — gerbang TIDAK sebulan (keputusan sadar): jendela bisa melintasi
    # batas bulan (dikirim hari terakhir, lewat hari pertama bulan berikutnya).
    # Saat lewat, status masih "awaiting" — jadi jendela lintas bulan tetap
    # harus boleh ditandai; gerbang sebulan akan menolaknya diam-diam.
    terakhir = _hari_terakhir_bulan_ini()
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(day=terakhir, hour=9, minute=0))
    dca_window.mark_missed(conn, _ts_berikutnya(hour=10))  # jendela sudah lewat
    assert _jumlah_missed(conn) == 1
    assert dca_window.dca_state(conn, _ts_berikutnya(hour=11))[0] == "missed"
