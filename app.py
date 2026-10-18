"""Punto d'ingresso dell'app: Crypto Radar e Stock Radar in un'unica finestra."""

import streamlit as st

import stock_radar
import trend_crypto

st.set_page_config(page_title="Radar Ultimate", page_icon="📡", layout="wide")
page = st.navigation([
    st.Page(trend_crypto.main, title="Crypto", icon="🪙", url_path="crypto", default=True),
    st.Page(stock_radar.main, title="Azioni & Istituzioni", icon="📈", url_path="azioni"),
])
page.run()
