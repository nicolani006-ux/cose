"""Pagine dell'app, usate dal menu (app.py) e dai link tra una pagina e l'altra."""

import streamlit as st


def crypto_page() -> st.Page:
    import trend_crypto

    return st.Page(trend_crypto.main, title="Crypto", icon="🪙", url_path="crypto", default=True)


def stocks_page() -> st.Page:
    import stock_radar

    return st.Page(stock_radar.main, title="Azioni & Istituzioni", icon="📈", url_path="azioni")


def settings_page() -> st.Page:
    import settings_page as sp

    return st.Page(sp.main, title="Impostazioni & Bot", icon="⚙️", url_path="impostazioni")


def test_page() -> st.Page:
    import test_page as tp

    return st.Page(tp.main, title="Test previsioni", icon="🧪", url_path="test")
