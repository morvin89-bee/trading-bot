"""Tab dasbor "Portofolio" — sistem pertumbuhan portofolio (read-only).

Menampilkan kepemilikan, saran take-profit/rebalance, progres DCA bulanan,
dan grafik pertumbuhan. Semua panggilan jaringan dibungkus `try/except`
supaya tab tidak pernah membuat dasbor crash. Modul ini tidak bisa dan
tidak akan mengirim order.
"""
import time

import pandas as pd
import plotly.express as px
import streamlit as st

import alerts
import growth_report
import portfolio
import portfolio_store
import rules

MONTHLY_IDR = rules.MONTHLY_IDR
READ_ONLY_BANNER = "Read-only — sistem ini tidak bisa mengirim order."


def fmt_idr(x):
    """Format rupiah gaya Indonesia: 1234567 -> 'Rp 1.234.567'."""
    try:
        return "Rp " + f"{float(x):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp 0"


def _holdings_rows(holdings, kas_idr=0.0):
    """Baris tabel kepemilikan; `kas_idr` = saldo IDR (uang tunai).

    Uang IDR tidak pernah jadi `Holding` (`portfolio.compute_holdings`
    melewati koin IDR) tapi tetap bagian portofolio — tanpa barisnya tabel
    tak penjumlah: jumlah kolom "Nilai (Rp)" ≠ caption total portofolio.
    """
    rows = []
    if kas_idr:
        rows.append({
            "Koin": "IDR",
            "Jumlah": fmt_idr(kas_idr),
            "Harga rata-rata": "tidak berlaku (uang tunai)",
            "Nilai (Rp)": round(kas_idr, 2),
            "Untung/Rugi belum terealisasi (Rp)": None,
        })
    for h in holdings:
        rows.append({
            "Koin": h.coin.upper(),
            "Jumlah": f"{h.qty:.8f}",
            "Harga rata-rata": (fmt_idr(h.avg_cost) if h.basis_known
                                else "harga rata-rata tidak diketahui"),
            "Nilai (Rp)": round(h.value_idr, 2),
            "Untung/Rugi belum terealisasi (Rp)": (
                round(h.unrealized_idr, 2)
                if h.unrealized_idr is not None else None),
        })
    return rows


def _render_growth(conn):
    snaps = portfolio_store.list_snapshots(conn)
    deps = portfolio_store.list_deposits(conn)
    rep = growth_report.monthly_report(snaps, deps)
    if not rep:
        st.info("Belum ada snapshot. Jalankan `python portfolio_cli.py snapshot` "
                "untuk mulai mencatat.")
        return
    df = pd.DataFrame(rep)
    fig = px.line(df, x="month", y="end_value_idr", markers=True,
                  labels={"month": "Bulan", "end_value_idr": "Nilai (Rp)"})
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(pd.DataFrame({
        "Bulan": df["month"],
        "Setoran (Rp)": df["contributions_idr"],
        "Hasil Pasar (Rp)": df["market_idr"],
        "Nilai Awal (Rp)": df["opening_value_idr"],
        "Nilai Akhir (Rp)": df["end_value_idr"],
    }), use_container_width=True)
    if not bool(df["opening_known"].all()):
        st.caption("Bulan tanpa snapshot SEBELUM setoran pertama: 'Hasil Pasar' "
                   "dihitung sejak setoran pertama (nilai awal 0, asumsi "
                   "portofolio kosong sebelum uang masuk).")


def render():
    st.subheader("Portofolio — Pertumbuhan & Take Profit")
    st.warning(READ_ONLY_BANNER)

    conn = None
    try:
        conn = portfolio_store.connect()
        portfolio_store.init_db(conn)
        out = portfolio.refresh(conn)
    except Exception as e:
        st.error(f"Gagal memuat data portofolio: {e}")
        if conn is not None:
            conn.close()
        return

    try:
        st.markdown("### Kepemilikan")
        kas_idr = out["total_idr"] - sum(h.value_idr for h in out["holdings"])
        rows = _holdings_rows(out["holdings"], kas_idr)
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("Belum ada aset yang dilacak.")
        st.caption(f"Total portofolio: {fmt_idr(out['total_idr'])}")
        realized = portfolio.realized_pnl(portfolio_store.list_trades(conn))
        if realized:
            st.caption("Keuntungan/kerugian yang sudah terealisasi (total): "
                       f"{fmt_idr(sum(realized.values()))}")

        st.markdown("### Saran Aksi (Take Profit / Rebalance)")
        msgs = alerts.build_alerts(out["holdings"], out["actions"])
        if not msgs:
            st.info("Tidak ada aksi yang disarankan saat ini. "
                    "Terus menabung rutin.")
        for m in msgs:
            st.warning(m)

        st.markdown("### Progres DCA Bulan Ini")
        values = {h.pair: h.value_idr for h in out["holdings"]}
        values["IDR"] = kas_idr
        saran = rules.dca_split(MONTHLY_IDR, values)
        deps = portfolio_store.list_deposits(conn)
        nyata = growth_report.month_contributions(deps, time.time())
        sisa = max(0.0, MONTHLY_IDR - nyata)
        status = "sudah penuh" if sisa <= 0 else f"kurang {fmt_idr(sisa)}"
        st.write(f"Setoran bulan ini: **{fmt_idr(nyata)}** dari target "
                 f"{fmt_idr(MONTHLY_IDR)} ({status}).")
        st.dataframe([{"Aset": k, "Alokasi yang disarankan": fmt_idr(v)}
                      for k, v in saran.items()], use_container_width=True)

        st.markdown("### Pertumbuhan Bulanan")
        _render_growth(conn)
    except Exception as e:
        st.error(f"Terjadi masalah menampilkan sebagian data: {e}")
    finally:
        conn.close()

    st.caption(READ_ONLY_BANNER)
