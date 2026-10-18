"""Punto d'ingresso dell'app: Crypto, Azioni e Impostazioni in un'unica finestra."""

import streamlit as st

import nav

st.set_page_config(page_title="Radar Ultimate", page_icon="📡", layout="wide")
page = st.navigation([nav.crypto_page(), nav.stocks_page(), nav.settings_page()])
page.run()
