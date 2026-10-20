"""Crypto Radar Ultimate - scanner di trend per criptovalute (Streamlit).

Avvio da sorgente:   streamlit run trend_crypto.py
Avvio come app:      python launcher.py   (finestra desktop)
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import ccxt
import feedparser
import pandas as pd
import requests
import streamlit as st

from charts import fmt_price, macd_chart, price_chart, rsi_chart  # noqa: F401
from ui_common import plan_metrics
from indicators import (  # noqa: F401
    HORIZON_BARS, PLAN_HELP, _crossed, add_indicators, analyze, atr, ema, macd, overall_stats, plan_and_stats, rsi,
    signal_label, stat_text,
)

APP_TITLE = "Crypto Radar Ultimate"

EXCHANGES = {
    "Binance": "binance",
    "Bybit": "bybit",
    "OKX": "okx",
    "KuCoin": "kucoin",
    "Gate.io": "gate",
    "MEXC": "mexc",
    "Kraken": "kraken",
}

TIMEFRAMES = {"15 minuti": "15m", "1 ora": "1h", "4 ore": "4h", "1 giorno": "1d"}

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD",
    "EUR", "EURT", "EURC", "EURI", "GBP", "USD", "USDE", "USD1", "RLUSD", "UST",
    "XUSD", "BFUSD", "AEUR", "WBTC", "WETH", "PAXG", "XAUT",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S", "2L", "2S")

NEWS_FEEDS = {
    "Cointelegraph": "https://cointelegraph.com/rss",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (CryptoRadarUltimate)"}
OHLCV_LIMIT = 1000  # candele scaricate: servono anche per la statistica storica dei segnali


def is_tradeable_base(base: str) -> bool:
    if base in STABLECOINS:
        return False
    return not any(base.endswith(s) and len(base) > len(s) for s in LEVERAGED_SUFFIXES)


# --------------------------------------------------------------------------- #
# Dati
# --------------------------------------------------------------------------- #

@st.cache_resource(show_spinner=False)
def get_exchange(exchange_id: str):
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": False, "timeout": 15000})
    ex.load_markets()
    return ex


def ohlcv_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop(columns="ts").dropna()


@st.cache_data(ttl=60, show_spinner=False)
def top_symbols(exchange_id: str, quote: str, n: int) -> pd.DataFrame:
    ex = get_exchange(exchange_id)
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m or not m.get("spot") or not m.get("active", True) or m.get("quote") != quote:
            continue
        if not is_tradeable_base(m["base"]):
            continue
        qv = t.get("quoteVolume")
        if qv is None and t.get("baseVolume") and t.get("last"):
            qv = t["baseVolume"] * t["last"]
        if not qv or not t.get("last"):
            continue
        rows.append({
            "symbol": sym,
            "base": m["base"],
            "last": t["last"],
            "change_24h": t.get("percentage"),
            "volume_24h": qv,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("volume_24h", ascending=False).head(n).reset_index(drop=True)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = OHLCV_LIMIT) -> pd.DataFrame:
    ex = get_exchange(exchange_id)
    return ohlcv_to_df(ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit))


@st.cache_data(ttl=60, show_spinner=False)
def scan(exchange_id: str, quote: str, n: int, timeframe: str) -> tuple[pd.DataFrame, list[str]]:
    tops = top_symbols(exchange_id, quote, n)
    if tops.empty:
        return tops, []
    ex = get_exchange(exchange_id)

    def work(row):
        try:
            df = ohlcv_to_df(ex.fetch_ohlcv(row["symbol"], timeframe=timeframe, limit=OHLCV_LIMIT))
            if len(df) < 60:
                return None, None
            return {**row, **analyze(df), **plan_and_stats(df, horizon=HORIZON_BARS.get(timeframe, 48))}, None
        except Exception as e:  # noqa: BLE001 - un simbolo non deve bloccare la scansione
            return None, f"{row['symbol']}: {e}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(work, tops.to_dict("records")))
    errors = [err for _, err in outcomes if err]
    out = pd.DataFrame([res for res, _ in outcomes if res])
    if not out.empty:
        out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out, errors


@st.cache_data(ttl=600, show_spinner=False)
def fear_greed() -> dict | None:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=2", timeout=10, headers=HTTP_HEADERS)
        data = r.json()["data"]
        return {
            "value": int(data[0]["value"]),
            "label": data[0]["value_classification"],
            "prev": int(data[1]["value"]) if len(data) > 1 else None,
        }
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_news(limit_per_feed: int = 10) -> pd.DataFrame:
    items = []
    for source, url in NEWS_FEEDS.items():
        try:
            resp = requests.get(url, timeout=10, headers=HTTP_HEADERS)
            feed = feedparser.parse(resp.content)
        except Exception:  # noqa: BLE001
            continue
        for e in feed.entries[:limit_per_feed]:
            published = None
            if e.get("published_parsed"):
                published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            items.append({
                "source": source,
                "title": e.get("title", ""),
                "link": e.get("link", ""),
                "published": published,
            })
    df = pd.DataFrame(items, columns=["source", "title", "link", "published"])
    if not df.empty:
        df = df.sort_values("published", ascending=False, na_position="last")
    return df


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #



EXPORT_COLUMNS = {
    "symbol": "Coppia", "price": "Prezzo", "change_24h": "Var. 24h %", "volume_24h": "Volume 24h",
    "rsi": "RSI", "trend_ema": "Trend EMA", "vol_ratio": "Volume rel.", "volatility": "Volatilità ATR %",
    "score": "Punteggio", "signal": "Segnale", "buy": "Prezzo BUY", "sell": "Prezzo SELL", "stop": "Stop loss",
    "bt_win_rate": "Successo storico %", "bt_trades": "Segnali storici", "bt_avg_ret": "Rendimento medio storico %",
    "alerts": "Allerte",
}


def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    out = df[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="Radar")
        ws = writer.sheets["Radar"]
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(width + 2, 60)
    return buf.getvalue()




# --------------------------------------------------------------------------- #
# Interfaccia
# --------------------------------------------------------------------------- #

def sidebar() -> dict:
    sb = st.sidebar
    sb.title("📡 " + APP_TITLE)
    ex_name = sb.selectbox("Exchange", list(EXCHANGES), index=0)
    quote = sb.selectbox("Valuta di quotazione", ["USDT", "USDC", "USD", "EUR", "BTC"], index=0)
    tf_name = sb.selectbox("Timeframe", list(TIMEFRAMES), index=1)
    n = sb.slider("Numero di coin (per volume)", 10, 100, 30, step=5)
    min_score = sb.slider("Punteggio minimo da mostrare", -100, 100, -100, step=10)
    only_alerts = sb.checkbox("Solo coin con allerte", value=False)
    auto = sb.checkbox("Aggiornamento automatico", value=False)
    interval = sb.select_slider("Ogni (secondi)", [30, 60, 120, 300, 600], value=60, disabled=not auto)
    if sb.button("🔄 Aggiorna ora", width="stretch"):
        st.cache_data.clear()
    sb.caption(
        "Il punteggio (-100…+100) combina medie mobili EMA 20/50/200, MACD, RSI, "
        "volume relativo e breakout. **Non è un consiglio finanziario.**"
    )
    return {
        "exchange": EXCHANGES[ex_name], "exchange_name": ex_name, "quote": quote,
        "timeframe": TIMEFRAMES[tf_name], "n": n, "min_score": min_score,
        "only_alerts": only_alerts, "auto": auto, "interval": interval,
    }


def auto_refresh(interval: int) -> None:
    """Riesegue l'app ogni `interval` secondi."""
    st.session_state.setdefault("last_refresh", time.time())

    @st.fragment(run_every=5)
    def _tick():
        if time.time() - st.session_state["last_refresh"] >= interval:
            st.session_state["last_refresh"] = time.time()
            st.cache_data.clear()
            st.rerun(scope="app")

    _tick()


def header_metrics(radar: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    fg = fear_greed()
    if fg:
        delta = fg["value"] - fg["prev"] if fg["prev"] is not None else None
        c1.metric("Fear & Greed", f"{fg['value']} – {fg['label']}", delta=delta, border=True)
    else:
        c1.metric("Fear & Greed", "n/d", border=True)
    bull = int((radar["score"] >= 20).sum())
    bear = int((radar["score"] <= -20).sum())
    c2.metric("Coin in rialzo", bull, border=True)
    c3.metric("Coin in ribasso", bear, border=True)
    c4.metric("Sentiment medio", f"{radar['score'].mean():+.0f}", border=True)
    stats = overall_stats(radar)
    if stats:
        st.caption(f"📊 Statistica storica dei segnali BUY su tutte le coin: **{stats['win_rate']:.0f}%** "
                   f"ha raggiunto il SELL prima dello STOP ({stats['trades']} operazioni simulate, "
                   f"rendimento medio {stats['avg_ret']:+.2f}%).", help=PLAN_HELP)


def tab_radar(radar: pd.DataFrame, cfg: dict) -> None:
    view = radar[radar["score"] >= cfg["min_score"]]
    if cfg["only_alerts"]:
        view = view[view["alerts"] != ""]
    st.caption(f"{len(view)} coin mostrate · {cfg['exchange_name']} · timeframe {cfg['timeframe']}")
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        height=min(38 * (len(view) + 1), 900),
        column_order=[
            "symbol", "price", "change_24h", "spark", "score", "signal", "buy", "sell", "stop",
            "bt_win_rate", "bt_trades", "rsi", "trend_ema", "vol_ratio", "volatility", "volume_24h", "alerts",
        ],
        column_config={
            "symbol": st.column_config.TextColumn("Coppia", pinned=True),
            "price": st.column_config.NumberColumn("Prezzo", format="%.6g"),
            "change_24h": st.column_config.NumberColumn("24h %", format="%+.2f%%"),
            "spark": st.column_config.LineChartColumn("Andamento", width="small"),
            "score": st.column_config.ProgressColumn(
                "Punteggio", min_value=-100, max_value=100, format="%+.0f"
            ),
            "signal": "Segnale",
            "rsi": st.column_config.NumberColumn("RSI", format="%.0f"),
            "trend_ema": "Trend EMA",
            "vol_ratio": st.column_config.NumberColumn("Vol. rel.", format="x%.2f"),
            "volatility": st.column_config.NumberColumn("Volatilità", format="%.2f%%"),
            "volume_24h": st.column_config.NumberColumn("Volume 24h", format="compact"),
            "alerts": "Allerte",
            "buy": st.column_config.NumberColumn("BUY", format="%.6g", help=PLAN_HELP),
            "sell": st.column_config.NumberColumn("SELL", format="%.6g", help=PLAN_HELP),
            "stop": st.column_config.NumberColumn("STOP", format="%.6g", help=PLAN_HELP),
            "bt_win_rate": st.column_config.NumberColumn("Successo storico", format="%.0f%%", help=PLAN_HELP),
            "bt_trades": st.column_config.NumberColumn("Segnali", format="%d"),
        },
    )


def tab_analysis(radar: pd.DataFrame, cfg: dict) -> None:
    symbol = st.selectbox("Coppia", radar["symbol"].tolist())
    row = radar.set_index("symbol").loc[symbol]
    df = add_indicators(fetch_ohlcv(cfg["exchange"], symbol, cfg["timeframe"]))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Prezzo", fmt_price(row["price"]),
              delta=f"{row['change_24h']:+.2f}% 24h" if pd.notna(row["change_24h"]) else None)
    c2.metric("Punteggio", f"{row['score']:+.0f}", delta=row["signal"], delta_color="off", delta_arrow="off")
    c3.metric("RSI (14)", f"{row['rsi']:.0f}")
    c4.metric("Volatilità (ATR)", f"{row['volatility']:.2f}%")
    if row["alerts"]:
        st.info("🔔 " + row["alerts"])
    plan_metrics(row)
    st.altair_chart(price_chart(df, symbol, {"BUY": row["buy"], "SELL": row["sell"], "STOP": row["stop"]}),
                    width="stretch")
    col_a, col_b = st.columns(2)
    col_a.altair_chart(rsi_chart(df), width="stretch")
    col_b.altair_chart(macd_chart(df), width="stretch")

    recent = df.tail(100)
    support, resistance = recent["low"].min(), recent["high"].max()
    last = df.iloc[-1]
    st.markdown(
        f"**Supporto (100 candele):** {fmt_price(support)} · "
        f"**Resistenza:** {fmt_price(resistance)} · "
        f"**Bollinger:** {fmt_price(last['bb_low'])} – {fmt_price(last['bb_up'])}"
    )


def tab_news() -> None:
    news = fetch_news()
    if news.empty:
        st.warning("Impossibile scaricare le notizie in questo momento.")
        return
    sources = st.multiselect("Fonti", sorted(news["source"].unique()), default=sorted(news["source"].unique()))
    for _, n in news[news["source"].isin(sources)].head(40).iterrows():
        when = n["published"].strftime("%d/%m %H:%M") if pd.notna(n["published"]) else ""
        st.markdown(f"**[{n['title']}]({n['link']})**  \n<small>{n['source']} · {when} UTC</small>",
                    unsafe_allow_html=True)


def tab_export(radar: pd.DataFrame) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    st.download_button(
        "📥 Scarica Excel", to_excel(radar), file_name=f"crypto_radar_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    csv = radar[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS).to_csv(index=False).encode("utf-8")
    st.download_button("📄 Scarica CSV", csv, file_name=f"crypto_radar_{stamp}.csv", mime="text/csv")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📡", layout="wide")
    cfg = sidebar()
    if cfg["auto"]:
        auto_refresh(cfg["interval"])

    st.title("📡 " + APP_TITLE)
    try:
        with st.spinner(f"Scansione di {cfg['n']} coin su {cfg['exchange_name']}…"):
            radar, errors = scan(cfg["exchange"], cfg["quote"], cfg["n"], cfg["timeframe"])
    except Exception as e:  # noqa: BLE001
        st.error(
            f"Impossibile contattare {cfg['exchange_name']}: {e}\n\n"
            "Controlla la connessione o prova un altro exchange dalla barra laterale."
        )
        return
    if radar.empty:
        st.warning(f"Nessuna coppia trovata su {cfg['exchange_name']} con quotazione {cfg['quote']}.")
        return
    if errors:
        with st.expander(f"⚠️ {len(errors)} coppie non scaricate"):
            st.write(errors)

    st.caption(f"Ultimo aggiornamento: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    header_metrics(radar)
    t1, t2, t3, t4 = st.tabs(["📡 Radar", "📈 Analisi", "📰 Notizie", "💾 Esporta"])
    with t1:
        tab_radar(radar, cfg)
    with t2:
        tab_analysis(radar, cfg)
    with t3:
        tab_news()
    with t4:
        tab_export(radar)


if __name__ == "__main__":
    main()
