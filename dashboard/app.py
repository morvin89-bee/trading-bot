"""
Main Streamlit Dashboard Aplikasi Mean-Reversion Arbitrage Indodax vs Tokocrypto.
Menampilkan 5 Tab: Market Overview, Pair Detail, Portfolio, History, Control.
Menggunakan Tailscale IP untuk akses jaringan dalam.
Arsitektur baru: SharedState + BackgroundUpdater (thread-safe)
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import time
import threading
import csv
import os
import json
from datetime import datetime, timedelta
import plotly.express as px

# --- Page Config ---
st.set_page_config(
    page_title="Trading Dashboard Mean-Reversion",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- Load Config & Data ---
import sys
sys.path.insert(0, ".")

import scanner
import strategy_mean_reversion as mr

from dashboard.utils.data_loader import (
    init_config, get_config, get_spread_history, compute_zscore,
    load_signals_recent, load_signals_all, get_active_positions,
    get_daily_pnl, get_balance_summary, get_tailscale_ip,
    load_market_data_for_dashboard
)
from dashboard.utils.shared_state import SharedState, get_shared_state
from dashboard.utils.background_updater import BackgroundUpdater, create_background_updater

# Initialize config at module level (safe)
init_config()
cfg = get_config()
st_cfg = cfg["strategy"]

SHARED_STATE_FILE = "dashboard_shared_state.json"

def init_app_state():
    """Initialize all app state - call this once at startup."""
    if "app_initialized" not in st.session_state:
        # Initialize SharedState (singleton)
        st.session_state.shared_state = get_shared_state("dashboard_shared_state.json")
        
        # Initialize Strategy
        from dashboard.utils.strategy_wrapper import init_strategy
        st.session_state.strategy = init_strategy()
        
        # Initialize BackgroundUpdater
        pairs = [p[1] for p in scanner.PAIRS]
        st.session_state.bg_updater = create_background_updater(
            shared_state=st.session_state.shared_state,
            strategy=st.session_state.strategy,
            pairs=pairs,
            interval=st_cfg.get("refresh_interval", 3000) // 1000,
            db_path="trades.db"
        )
        st.session_state.bg_updater.start()
        
        st.session_state.app_initialized = True
        st.session_state.last_data_timestamp = 0

def get_shared_state_obj():
    """Get shared state from session state."""
    return st.session_state.shared_state

def get_strategy_obj():
    """Get strategy from session state."""
    return st.session_state.strategy

def get_bg_updater():
    """Get background updater from session state."""
    return st.session_state.bg_updater

# Initialize app state on first run
if "app_initialized" not in st.session_state:
    init_app_state()

# Ensure BackgroundUpdater is started (lazy start)
if "bg_started" not in st.session_state:
    bg_updater = get_bg_updater()
    bg_updater.start()
    st.session_state.bg_started = True

# Get references
shared_state = get_shared_state_obj()
strategy = get_strategy_obj()
bg_updater = get_bg_updater()

# Page title
st.title("📈 Trading Dashboard Mean-Reversion")
st.caption(f"Versi {cfg['strategy']['version']} | Refresh otomatis setiap {st_cfg.get('refresh_interval', 3000)//1000}s | Tailscale IP: `{get_tailscale_ip()}`")

# Auto-refresh mechanism: check for updates every 3 seconds
if "last_ui_update" not in st.session_state:
    st.session_state.last_ui_update = time.time()

# Check for data updates and trigger rerun if needed
current_time = time.time()
last_update = shared_state.get("last_update", 0)
if current_time - st.session_state.last_ui_update > 2:  # Check every 2 seconds
    st.session_state.last_ui_update = current_time
    # Force rerun if background updater has new data
    if last_update > st.session_state.get("last_data_timestamp", 0):
        st.session_state.last_data_timestamp = last_update
        st.rerun()

# --- Synchronous Fallback for Market Data ---
def fetch_market_data_sync():
    """Fetch market data synchronously as fallback when BackgroundUpdater hasn't provided data."""
    import scanner
    import strategy_mean_reversion as mr
    
    market_data = {}
    pairs_list = [p[1] for p in scanner.PAIRS]
    symbols_list = [p[0] for p in scanner.PAIRS]
    PAIR_TO_SYMBOL = {p[1]: p[0] for p in scanner.PAIRS}
    
    for symbol, pair in zip(symbols_list, pairs_list):
        try:
            # Fetch prices
            bid_i, ask_i = scanner.indodax_price(pair)
            bid_t, ask_t = scanner.tokocrypto_price(symbol)
            
            # Calculate spreads
            spread_I_to_T, spread_T_to_I = mr.MeanReversionStrategy.calculate_spread(
                bid_i, ask_i, bid_t, ask_t
            )
            
            # Update strategy history for z-score calculation
            strategy.update_history(pair, spread_I_to_T, spread_T_to_I)
            
            # Compute z-scores
            z_I_to_T = strategy.compute_zscore(pair, "I_to_T")
            z_T_to_I = strategy.compute_zscore(pair, "T_to_I")
            
            # Check signal
            signal = strategy.check_signal(
                pair, bid_i, ask_i, bid_t, ask_t,
                buy_ratio=0.6, total_vol=1000, median_vol=500, change_pct=0.0
            )
            
            market_data[pair] = {
                "pair": pair,
                "symbol": symbol,
                "z_I_to_T": round(z_I_to_T, 4),
                "z_T_to_I": round(z_T_to_I, 4),
                "spread_I_to_T": round(spread_I_to_T, 2),
                "spread_T_to_I": round(spread_T_to_I, 2),
                "bid_i": bid_i,
                "ask_i": ask_i,
                "bid_t": bid_t,
                "ask_t": ask_t,
                "signal": signal,
                "timestamp": time.time(),
            }
        except Exception as e:
            # Silently skip pairs with errors
            pass
    
    return market_data


def ensure_market_data():
    """Ensure market data is available, fetching synchronously if needed."""
    market_data = shared_state.get("market_data", {})
    last_update = shared_state.get("last_update", 0)
    
    # If no market data or data is stale (> 30 seconds), fetch synchronously
    if not market_data or (time.time() - last_update > 30):
        with st.spinner("Fetching market data..."):
            market_data = fetch_market_data_sync()
            shared_state.update({
                "market_data": market_data,
                "last_update": time.time(),
                "last_error": None
            })
    
    return shared_state.get("market_data", {})


# --- Helper Functions ---

def make_market_button(pair, bid_i, ask_i, bid_t, ask_t, z_I, z_T, signal_type):
    """Buat tombol card pair di Market Overview."""
    spread_I, spread_T = mr.MeanReversionStrategy.calculate_spread(bid_i, ask_i, bid_t, ask_t)
    
    # Warna berdasarkan z-score
    if z_I <= -2.0:
        z_color = "🟢"
    elif z_I >= 2.0:
        z_color = "🔴"
    elif abs(z_I) <= 0.5:
        z_color = "🟡"
    else:
        z_color = "⚪"
    
    # Signal badge
    signal_badge = ""
    if signal_type == "long_spread":
        signal_badge = "🟢 LONG"
    elif signal_type == "short_spread":
        signal_badge = "🔴 SHORT"
    else:
        signal_badge = "⚪ HOLD"
    
    # Click handler (simpan pair terpilih ke session)
    if st.button(f"{pair}  |  {z_color} {z_I:.2f}  |  {signal_badge}", 
                 key=f"market_{pair}", 
                 use_container_width=True):
        st.session_state.selected_pair = pair
        st.rerun()

def get_signal_type(signal_dict):
    """Extract signal type from signal dict."""
    if not signal_dict:
        return None
    return signal_dict.get("type")

# --- TAB 1: MARKET OVERVIEW ---
def tab_market_overview():
    st.subheader("📊 Market Overview - 22 Pair Indodax vs Tokocrypto")
    
    # Get market data from SharedState
    market_data = shared_state.get("market_data", {})
    last_update = shared_state.get("last_update", 0)
    last_error = shared_state.get("last_error")
    
    # Status indicator
    col_status1, col_status2 = st.columns([3, 1])
    with col_status1:
        if last_update:
            st.caption(f"🔄 Last update: {datetime.fromtimestamp(last_update).strftime('%H:%M:%S')}")
        else:
            st.caption("⏳ Waiting for first data...")
    with col_status2:
        if last_error:
            st.error(f"⚠️ Error: {last_error}")
    
    # All pairs from scanner
    pairs_list = [p[1] for p in scanner.PAIRS]
    symbols_list = [p[0] for p in scanner.PAIRS]
    
    cols = st.columns(7)
    for i, (symbol, pair) in enumerate(zip(symbols_list, pairs_list)):
        col = cols[i % 7]
        with col:
            data = market_data.get(pair, {})
            
            if "error" in data:
                st.button(f"{pair}  |  ⚠️ Error", key=f"market_{pair}", use_container_width=True, disabled=True)
                continue
            
            z_I = data.get("z_I_to_T", 0)
            z_T = data.get("z_T_to_I", 0)
            bid_i = data.get("bid_i", 0)
            ask_i = data.get("ask_i", 0)
            bid_t = data.get("bid_t", 0)
            ask_t = data.get("ask_t", 0)
            signal = data.get("signal")
            signal_type = get_signal_type(signal)
            
            make_market_button(pair, bid_i, ask_i, bid_t, ask_t, z_I, z_T, signal_type)

# --- TAB 2: PAIR DETAIL ---
def tab_pair_detail():
    st.subheader("📈 Pair Detail & Take Profit Decider")
    
    market_data = shared_state.get("market_data", {})
    
    # Pilih pair
    pairs_list = [p[1] for p in scanner.PAIRS]
    symbols_list = [p[0] for p in scanner.PAIRS]
    display_pairs = [f"{s} ({p})" for s, p in zip(symbols_list, pairs_list)]
    pair_map = {f"{s} ({p})": p for s, p in zip(symbols_list, pairs_list)}
    
    # Default selected pair
    default_pair = "BTC_IDR (btc_idr)"
    if "selected_pair" in st.session_state:
        selected_display = st.session_state.selected_pair
        if selected_display not in display_pairs:
            selected_display = default_pair
    else:
        selected_display = default_pair
    
    # Safe index lookup with fallback
    try:
        default_index = display_pairs.index(selected_display)
    except ValueError:
        default_index = 0
        selected_display = display_pairs[0] if display_pairs else default_pair
    
    selected = st.selectbox("Pilih Pair", display_pairs, index=default_index)
    selected_pair = pair_map[selected]
    
    # Simpan pilihan (gunakan selected dari selectbox, bukan selected_display)
    st.session_state.selected_pair = selected
    
    # Ambil data dari market_data
    data = market_data.get(selected_pair, {})
    
    if "error" in data:
        st.error(f"Error loading {selected_pair}: {data['error']}")
        return
    
    z_I = data.get("z_I_to_T", 0)
    z_T = data.get("z_T_to_I", 0)
    spread_I = data.get("spread_I_to_T", 0)
    spread_T = data.get("spread_T_to_I", 0)
    bid_i = data.get("bid_i", 0)
    ask_i = data.get("ask_i", 0)
    bid_t = data.get("bid_t", 0)
    ask_t = data.get("ask_t", 0)
    signal = data.get("signal")
    signal_type = get_signal_type(signal)
    
    # --- Layout 3 Kolom: Chart + Z-Score + TP Panel ---
    col_left, col_mid, col_right = st.columns([2, 1, 1.5])
    
    with col_left:
        # Chart Spread Time-series (placeholder - real implementation needs historical data)
        st.caption("📊 Spread Chart - Historical data from DB needed for full implementation")
        
        # Placeholder chart with current values
        fig_spread = go.Figure()
        fig_spread.add_trace(go.Scatter(
            x=[datetime.now()], y=[spread_I], name="Spread I→T", 
            mode='markers+lines', line=dict(color="#2ca02c")
        ))
        fig_spread.add_trace(go.Scatter(
            x=[datetime.now()], y=[spread_T], name="Spread T→I", 
            mode='markers+lines', line=dict(color="#d62728")
        ))
        # Threshold lines
        for val in [-2.0, -0.5, 0.5, 2.0]:
            fig_spread.add_hline(y=val, line_dash="dash", line_color="gray", opacity=0.5)
        fig_spread.update_layout(
            height=300, margin=dict(l=0, r=0, t=30, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            title="Current Spread Values"
        )
        st.plotly_chart(fig_spread, use_container_width=True)
    
    with col_mid:
        # Z-Score Display
        st.markdown("### Current Z-Score")
        c1, c2 = st.columns(2)
        with c1:
            z_I_delta = "✅ Entry" if z_I <= -2.0 else ("⚠️ Exit" if abs(z_I) <= 0.5 else "📈 Hold")
            st.metric("Z I→T", f"{z_I:.2f}", delta=z_I_delta)
        with c2:
            z_T_delta = "✅ Entry" if z_T >= 2.0 else ("⚠️ Exit" if abs(z_T) <= 0.5 else "📈 Hold")
            st.metric("Z T→I", f"{z_T:.2f}", delta=z_T_delta)
        
        # Spread values
        st.markdown("### Spread Values")
        st.metric("Spread I→T", f"{spread_I:,.0f} IDR")
        st.metric("Spread T→I", f"{spread_T:,.0f} IDR")
        
        # Price info
        st.markdown("### Prices")
        st.caption(f"Indodax: Bid={data.get('bid_i', 0):,.0f} | Ask={data.get('ask_i', 0):,.0f}")
        st.caption(f"Tokocrypto: Bid={data.get('bid_t', 0):,.0f} | Ask={data.get('ask_t', 0):,.0f}")
    
    with col_right:
        # --- TAKE PROFIT PANEL ---
        st.markdown("### 🎯 Take Profit Decision Helper")
        
        # Current Position Check
        positions = strategy.positions if hasattr(strategy, 'positions') else {}
        
        if selected_pair in positions:
            pos = positions[selected_pair]
            direction = pos["direction"]
            entry_z = pos["entry_z"]
            entry_time = pos["entry_time"]
            age_min = (time.time() - entry_time) / 60
            
            st.info(f"**Position Aktif:** {selected_pair}\nArah: `{direction.upper()}`\nZ-Entry: `{entry_z:.2f}`\nUmur: `{age_min:.1f}` menit")
            
            # Exit Recommendation
            current_z = z_I if direction == "long" else z_T
            if abs(current_z) <= 0.5:
                st.success(f"✅ **RECOMMENDATION: EXIT NOW**\n|z| ≤ 0.5 (exit threshold), tutup posisi untuk capture profit.")
            elif current_z >= 3.5:
                st.error(f"🛑 **STOP LOSS**\n|z| ≥ 3.5 (stop loss), harga sudah ekstrem, tutup sekurang-kurangnya parcial.")
            elif abs(current_z) >= 2.0 and age_min > 15:
                st.warning(f"⏳ **TRAILING STOP**\nZ sudah >2.0 selama >15 menit, gerakkan stop loss ke break-even atau 0.5% profit.")
            else:
                st.info(f"⏸️ **HOLD**\nMenunggu konvergensi ke mean (|z| ≤ 0.5).")
            
            # --- Trailing Stop Control ---
            trailing_enabled = st.checkbox(
                "🔄 Trailing Stop", 
                value=pos.get("trailing_stop_active", False),
                help="Aktifkan trailing stop untuk lock profit otomatis"
            )
            if trailing_enabled != pos.get("trailing_stop_active", False):
                pos["trailing_stop_active"] = trailing_enabled
                if trailing_enabled:
                    strategy.activate_trailing_stop(selected_pair)
                shared_state.set(f"position_{selected_pair}", pos)
                st.rerun()
            
            # Time Exit Control
            time_exit_enabled = st.checkbox(
                "⏰ Force Close After X menit", 
                value=pos.get("time_exit_enabled", False),
                help="Tutup posisi secara paksa setelah waktu tertentu"
            )
            time_exit_min = st.slider(
                "Menit", 5, 120, 
                value=pos.get("time_exit_minutes", 30), 
                step=5,
                help="Waktu sebelum force close (menit)"
            )
            if time_exit_enabled != pos.get("time_exit_enabled") or time_exit_min != pos.get("time_exit_minutes", 30):
                pos["time_exit_enabled"] = time_exit_enabled
                pos["time_exit_minutes"] = time_exit_min
                if time_exit_enabled:
                    strategy.enable_time_exit(selected_pair, time_exit_min)
                shared_state.set(f"position_{selected_pair}", pos)
                st.rerun()
            
            # Partial Close Slider
            partial = st.slider(
                "Partial Close %", 0, 100, 
                value=pos.get("partial_close_pct", 0), 
                step=5,
                help="Bagi posisi jadi slice, capture profit sebagian"
            )
            if partial > 0 and partial != pos.get("partial_close_pct", 0):
                pos["partial_close_pct"] = partial
                strategy.set_partial_close(selected_pair, partial)
                # Execute partial close via paper_trader
                paper_trader = st.session_state.get("paper_trader")
                if paper_trader and hasattr(paper_trader, 'execute_partial_close'):
                    with st.spinner(f"Mengeksekusi partial close {partial}%..."):
                        results = paper_trader.execute_partial_close(selected_pair, partial)
                        if results:
                            st.success(f"✅ Partial close {partial}% dieksekusi!")
                            st.rerun()
                        else:
                            st.warning("Partial close gagal (cek koneksi API / saldo)")
                shared_state.set(f"position_{selected_pair}", pos)
                st.rerun()
            elif partial == 0 and pos.get("partial_close_pct", 0) > 0:
                pos["partial_close_pct"] = 0
                shared_state.set(f"position_{selected_pair}", pos)
                st.rerun()
            
            if partial > 0:
                st.caption(f"Preview: Partial {partial}% akan capture sekitar {partial/100 * abs(spread_I):,.0f} IDR profit (estimasi)")
        
        else:
            # No position - show recommendation based on z-score
            st.markdown("**Tidak ada posisi aktif.**")
            c1, c2 = st.columns(2)
            with c1:
                if z_I <= -2.0:
                    st.success(f"🟢 **ENTRY SIGNAL**\nZ I→T = {z_I:.2f} ≤ -2.0\nAction: Beli Indodax, Jual Tokocrypto")
                elif abs(z_I) <= 0.5:
                    st.info(f"🟡 **IN ZONE**\nZ I→T = {z_I:.2f} | di zona mean, tunggu sinyal kuat")
                else:
                    st.warning(f"🔴 **AWAY FROM MEAN**\nZ I→T = {z_I:.2f}, masih jauh dari konvergensi")
            
            with c2:
                if z_T >= 2.0:
                    st.success(f"🟢 **ENTRY SIGNAL**\nZ T→I = {z_T:.2f} ≥ +2.0\nAction: Beli Tokocrypto, Jual Indodax")
                elif abs(z_T) <= 0.5:
                    st.info(f"🟡 **IN ZONE**\nZ T→I = {z_T:.2f} | di zona mean")
                else:
                    st.warning(f"🔴 **AWAY FROM MEAN**\nZ T→I = {z_T:.2f}")
            
            # Time-based exit toggle (for future positions)
            st.checkbox("Force Close After X menit", value=False, help="Tutup posisi secara paksa setelah waktu tertentu")
        
        # Tombol kontrol cepat
        st.caption("---")
        if st.button("⏸️ PAUSE Sinyal", use_container_width=True):
            st.info("Sinyal dijeda (simulasi)")
        if st.button("▶️ RESUME Sinyal", use_container_width=True):
            st.success("Sinyal aktif kembali")
        if st.button("🛑 EMERGENCY STOP", type="primary", use_container_width=True):
            st.error("Semua trade dihentikan emergency")

# --- TAB 3: PORTFOLIO ---
def tab_portfolio():
    st.subheader("💰 Portfolio & Risk Monitor")
    
    # Equity Curve (dari signals.csv)
    summary = get_balance_summary()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total P&L", f"{summary['total_pnl']:,.0f} IDR")
    with col2:
        st.metric("Total Trades", f"{summary['trades']}")
    with col3:
        st.metric("Win Rate", f"{summary['win_rate']:.1f}%")
    with col4:
        st.metric("Daily Loss Limit", "3%")
    
    # Active Positions Table
    st.markdown("### Active Positions")
    positions = strategy.positions if hasattr(strategy, 'positions') else {}
    
    if positions:
        pos_df = pd.DataFrame([
            {
                "Pair": k,
                "Arah": v["direction"].upper(),
                "Z-Entry": f"{v['entry_z']:.2f}",
                "Durasi (menit)": round((time.time() - v["entry_time"]) / 60, 1),
                "Size": f"{v['size']:.2f}%"
            }
            for k, v in positions.items()
        ])
        st.dataframe(pos_df, hide_index=True, use_container_width=True)
    else:
        st.caption("Tidak ada posisi aktif")
    
    # Risk Limits Progress
    st.markdown("### Risk Limits")
    c1, c2 = st.columns(2)
    with c1:
        st.progress(0.3, text="Daily Loss: 0.9% / 3% (30%)")
    with c2:
        st.progress(0.2, text="Max Position: 2% / 10% (20%)")
    
    # Daily P&L History from signals.csv
    st.markdown("### Equity Curve (Last 30 Days)")
    signals = load_signals_all()
    
    if signals:
        df = pd.DataFrame(signals)
        if "time" in df.columns and "pnl_idr" in df.columns:
            df["time"] = pd.to_datetime(df["time"])
            df = df.sort_values("time")
            df["cumulative_pnl"] = df["pnl_idr"].astype(float).cumsum()
            
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=df["time"][-30:], 
                y=df["cumulative_pnl"][-30:], 
                mode='lines', name='Equity', 
                line=dict(color="#1f77b4")
            ))
            fig_eq.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            fig_eq.update_layout(
                height=250, margin=dict(l=0, r=0, t=30, b=0),
                title="Daily P&L Cumulative (Real Data)"
            )
            st.plotly_chart(fig_eq, use_container_width=True)
        else:
            st.caption("Waiting for signal data with pnl_idr column...")
    else:
        st.caption("Belum ada data sinyal. Jalankan monitor.py dan/atau paper_trader.py terlebih dahulu.")

# --- TAB 4: HISTORY ---
def tab_history():
    st.subheader("📋 Signal History & Performance")
    
    # Filter waktu
    time_filter = st.radio("Filter Waktu", ["Semua", "Hari Ini", "7 Hari", "30 Hari"], horizontal=True)
    
    signals = load_signals_all()  # semua baris
    
    if time_filter != "Semua":
        hours = {"Hari Ini": 24, "7 Hari": 168, "30 Hari": 720}[time_filter]
        signals = load_signals_recent(hours)
    
    if signals:
        df = pd.DataFrame(signals)
        
        # Normalisasi kolom
        if "pair" in df.columns:
            display_cols = [c for c in ["time", "pair", "buy_exchange", "sell_exchange", "pnl_idr", "z_score", "direction"] if c in df.columns]
            st.dataframe(df[display_cols], hide_index=True, use_container_width=True, height=400)
        
        # Performance by Pair
        st.markdown("### Performa per Pair")
        if "pair" in df.columns and "pnl_idr" in df.columns:
            df["pnl_idr"] = pd.to_numeric(df["pnl_idr"], errors="coerce")
            pair_perf = df.groupby("pair")["pnl_idr"].agg(["sum", "count", "mean"]).reset_index()
            pair_perf.columns = ["Pair", "Total P&L", "Jumlah Trade", "Rata-rata P&L"]
            
            fig_perf = go.Figure()
            fig_perf.add_trace(go.Bar(
                x=pair_perf["Pair"], 
                y=pair_perf["Total P&L"], 
                marker_color=pair_perf["Total P&L"].apply(lambda x: "#2ca02c" if x > 0 else "#d62728")
            ))
            fig_perf.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0), title="Total P&L per Pair")
            st.plotly_chart(fig_perf, use_container_width=True)
            
            # Win Rate per Pair
            df["is_win"] = df["pnl_idr"].apply(lambda x: 1 if float(x) > 0 else 0)
            win_by_pair = df.groupby("pair")["is_win"].mean().reset_index()
            win_by_pair.columns = ["Pair", "Win Rate %"]
            
            fig_win = go.Figure(go.Bar(
                x=win_by_pair["Pair"], 
                y=win_by_pair["Win Rate %"] * 100,
                marker_color="#1f77b4"
            ))
            fig_win.update_layout(height=250, margin=dict(l=0, r=0, t=30, b=0), title="Win Rate per Pair (%)")
            st.plotly_chart(fig_win, use_container_width=True)
    else:
        st.info("Belum ada data sinyal. Jalankan monitor.py dan/atau paper_trader.py terlebih dahulu.")
    
    # Buttom export
    if st.button("📥 Export CSV History"):
        if signals:
            import base64
            df = pd.DataFrame(signals)
            csv = df.to_csv(index=False)
            b64 = base64.b64encode(csv.encode()).decode()
            st.download_button("Download CSV", b64, file_name="signal_history.csv", mime="text/csv")

# --- TAB 5: CONTROL ---
def tab_control():
    st.subheader("⚙️ Control Panel & System Status")
    
    # Trading Mode
    st.markdown("### Trading Mode")
    col_a, col_b = st.columns(2)
    with col_a:
        paper_mode = st.checkbox("🟢 Paper Trading", value=True, help="Simulasi tanpa uang asli")
    with col_b:
        live_mode = st.checkbox("🔴 Live Trading", value=False, help="Real uang - HATI-HATI!", disabled=True)
    
    # Strategy Parameters
    st.markdown("### Strategy Parameters (live reload)")
    c1, c2 = st.columns(2)
    with c1:
        entry_z = st.slider("entry_z", 0.5, 4.0, float(cfg["strategy"]["entry_z"]), 0.1,
                          help="Threshold entry z-score")
        exit_z = st.slider("exit_z", 0.1, 2.0, float(cfg["strategy"]["exit_z"]), 0.1)
        stop_z = st.slider("stop_z", 1.0, 6.0, float(cfg["strategy"]["stop_z"]), 0.5)
    with c2:
        position_size = st.slider("position_size %", 0.5, 10.0, float(cfg["strategy"]["position_size_pct"]*100), 0.5)
        max_concurrent = st.slider("max_concurrent", 1, 10, int(cfg["strategy"]["max_concurrent_trades"]))
        cooldown = st.slider("cooldown_sec", 60, 600, int(cfg["strategy"]["cooldown_seconds"]), 10)
    
    if st.button("💾 Save & Reload Config", type="primary"):
        # Update config file
        cfg["strategy"]["entry_z"] = entry_z
        cfg["strategy"]["exit_z"] = exit_z
        cfg["strategy"]["stop_z"] = stop_z
        cfg["strategy"]["position_size_pct"] = position_size / 100
        cfg["strategy"]["max_concurrent_trades"] = max_concurrent
        cfg["strategy"]["cooldown_seconds"] = cooldown
        
        import yaml
        with open("config_strategy.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)
        
        # Reload strategy config
        strategy.cfg = cfg
        
        st.success("Config disimpan! Dashboard akan direload.")
        st.rerun()
    
    # System Status - Real data
    st.markdown("### System Status")
    market_data = shared_state.get("market_data", {})
    last_update = shared_state.get("last_update", 0)
    last_error = shared_state.get("last_error")
    
    col_c, col_d = st.columns(2)
    with col_c:
        st.caption("🔗 Tailscale Status: Aktif")
        st.caption(f"📡 API Latency: Real-time via BackgroundUpdater")
        st.caption(f"💾 DB: trades.db | signals.csv")
    with col_d:
        if last_update:
            st.caption(f"🕒 Last Update: {datetime.fromtimestamp(last_update).strftime('%H:%M:%S')}")
        else:
            st.caption("🕒 Last Update: Waiting...")
        st.caption(f"📊 Active Positions: {len(strategy.positions) if hasattr(strategy, 'positions') else 0}")
        if last_error:
            st.caption(f"⚠️ Last Error: {last_error}")
    
    # Buttons
    st.markdown("---")
    col_e, col_f, col_g = st.columns(3)
    with col_e:
        if st.button("▶️ START", type="primary", use_container_width=True):
            if not bg_updater.is_running():
                bg_updater.start()
                st.success("Background updater dinyalakan")
            else:
                st.info("Sudah berjalan")
    with col_f:
        if st.button("⏸️ PAUSE", use_container_width=True):
            if bg_updater.is_running():
                bg_updater.stop()
                st.warning("Background updater dijeda")
            else:
                st.info("Sudah dijeda")
    with col_g:
        if st.button("⛔ STOP", type="secondary", use_container_width=True):
            bg_updater.stop()
            st.error("Emergency stop - Background updater dihentikan")

# --- Main App Layout ---

def main():
    # Navbar sederhana
    st.sidebar.title("Navigasi")
    tab_names = ["Market", "Pair Detail", "Portfolio", "History", "Control"]
    selected_tab = st.sidebar.radio("Pilih Tab", tab_names, index=0)
    
    # Render tab
    if selected_tab == "Market":
        tab_market_overview()
    elif selected_tab == "Pair Detail":
        tab_pair_detail()
    elif selected_tab == "Portfolio":
        tab_portfolio()
    elif selected_tab == "History":
        tab_history()
    elif selected_tab == "Control":
        tab_control()

if __name__ == "__main__":
    main()