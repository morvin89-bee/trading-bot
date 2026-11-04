"""Dashboard bot semi-otomatis (read-only)."""
import streamlit as st

st.set_page_config(page_title="Trading Bot", page_icon="📈", layout="wide")

st.sidebar.title("Navigasi")
pilihan = st.sidebar.radio("Pilih Tab", ["Portofolio", "Shadow"])

if pilihan == "Portofolio":
    from dashboard.portfolio_tab import render
    render()
else:
    from dashboard.shadow_tab import render
    render()
