"""Tab dasbor "Shadow" — pantau periode bayangan G5 (read-only).

Menunjukkan hari keberapa shadow berjalan, status sakelar eksekusi,
rekap penolakan hari ini, dan ringkasan log usulan terakhir. Semua baca
`portfolio.db` saja — modul ini tidak bisa dan tidak akan mengirim order.
"""
import sqlite3
import time

import pandas as pd
import streamlit as st

import main_loop
import portfolio_store

HARI_TARGET = main_loop.GO_LIVE_SHADOW_DAYS

# Terjemahan kode hasil ke bahasa manusia. Kode asing tampil apa adanya —
# menghapusnya akan menyembunyikan kejadian nyata dari pemantau.
_KODE = {
    "proposed": "usulan dikirim",
    "setuju": "disetujui",
    "tunda": "ditunda",
    "batal": "dibatalkan",
    "expired": "usulan kedaluwarsa",
    "spread_rejected": "ditolak penyebaran harga",
    "gate_rejected": "ditolak gerbang pemeriksa",
    "dca_missed": "DCA terlewat",
    "halted": "dihentikan",
    "executed": "dieksekusi",
}

_READ_ONLY = "Read-only — sistem ini tidak bisa mengirim order."


def sisa_hari(shadow_day):
    """Berapa hari lagi menuju syarat minimum G5. Tidak pernah negatif."""
    return max(0, HARI_TARGET - int(shadow_day))


def status_sakelar(v1_enabled):
    """Status sakelar eksekusi dalam bahasa manusia."""
    if v1_enabled:
        return "MENYALA ⚠️"
    return "mati (aman)"


def bahasa_hasil(outcome):
    """Kode hasil shadow_log → bahasa Indonesia."""
    return _KODE.get(outcome, outcome)


def rekap_hasil(baris):
    """`baris` pasangan (outcome, jumlah) → dict penghitungan per jenis."""
    rekap = {}
    for outcome, jumlah in baris:
        rekap[outcome] = rekap.get(outcome, 0) + jumlah
    return rekap


def teks_rekap(rekap):
    """Ringkasan satu kalimat, jenis terbanyak lebih dulu. Jujur soal nol."""
    if not rekap:
        return "Belum ada usulan — nol penolakan."
    urut = sorted(rekap.items(), key=lambda kv: kv[1], reverse=True)
    bagian = ", ".join(f"{n} {bahasa_hasil(k)}" for k, n in urut)
    if not any("rejected" in k for k in rekap):
        return f"{bagian} — nol penolakan."
    return bagian


def _detik_ke_waktu(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def render():
    st.subheader("Periode Bayangan (Shadow) — G5")
    st.warning(_READ_ONLY)

    conn = portfolio_store.connect()
    try:
        _kartu_status(conn)
        st.divider()
        _rekap_hari_ini(conn)
        _log_terakhir(conn)
    finally:
        conn.close()


def _kartu_status(conn):
    baris = conn.execute(
        "SELECT shadow_day, v1_enabled, shadow_checked_day, dca_status "
        "FROM bot_state WHERE id = 1").fetchone()
    if baris is None:
        st.info("Belum ada catatan periode shadow.")
        return
    shadow_day, v1_enabled, diperiksa, dca_status = baris

    col1, col2, col3 = st.columns(3)
    col1.metric("Hari ke-", f"{shadow_day} dari {HARI_TARGET}")
    col2.metric("Sisa menuju syarat", f"{sisa_hari(shadow_day)} hari")
    col3.metric("Sakelar eksekusi", status_sakelar(v1_enabled))

    if v1_enabled:
        st.error(
            "Sakelar eksekusi MENYALAH. Tanpa pabrik transport order tetap "
            "mustahil terkirim — tapi ini pantau dengan teliti."
        )
    st.caption(
        f"Pengecekan hari terakhir: {diperiksa or '—'} · "
        f"Status DCA bulan ini: {dca_status or '—'}"
    )


def _rekap_hari_ini(conn):
    st.subheader("Rekap hari ini")
    hari_ini = time.strftime("%Y-%m-%d", time.localtime())
    try:
        baris = conn.execute(
            "SELECT outcome, COUNT(*) FROM proposals WHERE day = ? "
            "GROUP BY outcome", (hari_ini,)).fetchall()
    except sqlite3.OperationalError:
        # Tabel proposals belum pernah dibuat = belum ada usulan sama sekali.
        st.info("Belum ada catatan usulan hari ini — nol penolakan.")
        return
    teks = teks_rekap(rekap_hasil(baris))
    if "ditolak" in teks:
        st.warning(teks)
    else:
        st.success(teks)


def _log_terakhir(conn, batas=20):
    st.subheader(f"{batas} usulan terakhir")
    try:
        baris = conn.execute(
            "SELECT ts, kind, pair, amount_idr, outcome, note "
            "FROM proposals ORDER BY ts DESC LIMIT ?", (batas,)).fetchall()
    except sqlite3.OperationalError:
        st.info("Belum ada log sama sekali.")
        return
    if not baris:
        st.info("Belum ada usulan yang tercatat.")
        return
    df = pd.DataFrame([{
        "Waktu": _detik_ke_waktu(ts),
        "Jenis": kind or "—",
        "Pasangan": (pair or "—").upper(),
        "Nilai": (f"Rp {amount_idr:,.0f}".replace(",", ".")
                  if amount_idr else "—"),
        "Hasil": bahasa_hasil(outcome),
        "Catatan": note or "",
    } for ts, kind, pair, amount_idr, outcome, note in baris])
    st.dataframe(df, hide_index=True, width="stretch")
