"""Crypto Radar Ultimate - scanner di trend per criptovalute (Streamlit).

Avvio da sorgente:   streamlit run trend_crypto.py
Avvio come app:      python launcher.py   (finestra desktop)
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import altair as alt
import ccxt
import feedparser
import numpy as np
import pandas as pd
import requests
import streamlit as st

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


# --------------------------------------------------------------------------- #
# Indicatori tecnici
# --------------------------------------------------------------------------- #

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI di Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # nessuna perdita nel periodo -> RSI 100
    out = out.where(~((loss == 0) & gain.notna()), 100.0)
    return out


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    df["ema20"] = ema(close, 20)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(close)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["bb_mid"], df["bb_up"], df["bb_low"] = mid, mid + 2 * std, mid - 2 * std
    df["atr"] = atr(df)
    return df


def _crossed(a: pd.Series, b: pd.Series, lookback: int) -> int:
    """+1 se `a` ha incrociato `b` verso l'alto nelle ultime `lookback` candele,
    -1 se verso il basso, 0 altrimenti."""
    diff = (a - b).dropna()
    if len(diff) < lookback + 1:
        return 0
    window = np.sign(diff.iloc[-(lookback + 1):].to_numpy())
    for prev, cur in zip(window[:-1], window[1:]):
        if prev <= 0 < cur:
            return 1
        if prev >= 0 > cur:
            return -1
    return 0


def analyze(df: pd.DataFrame) -> dict:
    """Calcola indicatori, punteggio di trend (-100..100) e segnali per una serie OHLCV."""
    df = add_indicators(df)
    last = df.iloc[-1]
    close = float(last["close"])
    has_ema200 = len(df) >= 200

    score = 0.0
    # Trend (medie mobili)
    score += 15 if close > last["ema20"] else -15
    score += 15 if last["ema20"] > last["ema50"] else -15
    if has_ema200:
        score += 20 if last["ema50"] > last["ema200"] else -20
    # Momentum (MACD)
    score += 10 if last["macd_hist"] > 0 else -10
    macd_cross = _crossed(df["macd"], df["macd_signal"], 3)
    score += 10 * macd_cross
    # RSI
    r = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    if 55 <= r < 70:
        score += 15
    elif r >= 70:
        score += 5  # forte ma ipercomprato
    elif 30 < r <= 45:
        score -= 15
    elif r <= 30:
        score -= 5  # debole ma ipervenduto
    # Volume
    vol_avg = df["volume"].iloc[-21:-1].mean()
    vol_ratio = float(last["volume"] / vol_avg) if vol_avg and vol_avg > 0 else 1.0
    if vol_ratio >= 1.5:
        score *= 1.2
    # Breakout sui massimi/minimi delle ultime 20 candele
    hi20 = df["high"].iloc[-21:-1].max()
    lo20 = df["low"].iloc[-21:-1].min()
    breakout = 1 if close > hi20 else (-1 if close < lo20 else 0)
    score += 10 * breakout
    score = float(np.clip(score, -100, 100))

    alerts = []
    if r >= 70:
        alerts.append("RSI ipercomprato")
    elif r <= 30:
        alerts.append("RSI ipervenduto")
    if has_ema200:
        gc = _crossed(df["ema50"], df["ema200"], 5)
        if gc == 1:
            alerts.append("Golden cross")
        elif gc == -1:
            alerts.append("Death cross")
    if macd_cross == 1:
        alerts.append("MACD incrocio rialzista")
    elif macd_cross == -1:
        alerts.append("MACD incrocio ribassista")
    if vol_ratio >= 2:
        alerts.append(f"Volume x{vol_ratio:.1f}")
    if breakout == 1:
        alerts.append("Breakout massimi 20")
    elif breakout == -1:
        alerts.append("Rottura minimi 20")

    first = float(df["close"].iloc[0])
    return {
        "price": close,
        "rsi": r,
        "macd_hist": float(last["macd_hist"]),
        "trend_ema": "Rialzista" if last["ema20"] > last["ema50"] else "Ribassista",
        "vol_ratio": vol_ratio,
        "volatility": float(last["atr"] / close * 100) if pd.notna(last["atr"]) and close else np.nan,
        "change_period": (close / first - 1) * 100 if first else np.nan,
        "score": round(score, 1),
        "signal": signal_label(score),
        "alerts": ", ".join(alerts),
        "spark": df["close"].iloc[-50:].round(10).tolist(),
    }


def signal_label(score: float) -> str:
    if score >= 50:
        return "🟢 FORTE RIALZO"
    if score >= 20:
        return "🟢 Rialzo"
    if score <= -50:
        return "🔴 FORTE RIBASSO"
    if score <= -20:
        return "🔴 Ribasso"
    return "⚪ Neutrale"


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
def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
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
            df = ohlcv_to_df(ex.fetch_ohlcv(row["symbol"], timeframe=timeframe, limit=300))
            if len(df) < 60:
                return None, None
            return {**row, **analyze(df)}, None
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
# Grafici ed export
# --------------------------------------------------------------------------- #

def price_chart(df: pd.DataFrame, symbol: str) -> alt.Chart:
    df = df.tail(150)
    base = alt.Chart(df).encode(x=alt.X("time:T", title=None))
    color = alt.condition("datum.open <= datum.close", alt.value("#26a69a"), alt.value("#ef5350"))
    wick = base.mark_rule().encode(
        y=alt.Y("low:Q", title=symbol, scale=alt.Scale(zero=False)), y2="high:Q", color=color
    )
    body = base.mark_bar().encode(
        y="open:Q",
        y2="close:Q",
        color=color,
        tooltip=[
            alt.Tooltip("time:T", title="Data", format="%d/%m %H:%M"),
            "open:Q", "high:Q", "low:Q", "close:Q",
        ],
    )
    lines = base.transform_fold(
        ["ema20", "ema50", "ema200"], as_=["Media", "valore"]
    ).mark_line(strokeWidth=1.5).encode(
        y="valore:Q",
        color=alt.Color(
            "Media:N",
            scale=alt.Scale(domain=["ema20", "ema50", "ema200"], range=["#f6c343", "#4c9aff", "#c77dff"]),
            legend=alt.Legend(orient="top", title=None),
        ),
    )
    return alt.layer(wick, body, lines).resolve_scale(color="independent").properties(height=420)


def rsi_chart(df: pd.DataFrame) -> alt.Chart:
    df = df.tail(150)
    line = alt.Chart(df).mark_line(color="#c77dff").encode(
        x=alt.X("time:T", title=None), y=alt.Y("rsi:Q", title="RSI", scale=alt.Scale(domain=[0, 100]))
    )
    bands = alt.Chart(pd.DataFrame({"y": [30, 70]})).mark_rule(strokeDash=[4, 4], color="gray").encode(y="y:Q")
    return (line + bands).properties(height=160)


def macd_chart(df: pd.DataFrame) -> alt.Chart:
    df = df.tail(150)
    base = alt.Chart(df).encode(x=alt.X("time:T", title=None))
    hist = base.mark_bar().encode(
        y=alt.Y("macd_hist:Q", title="MACD"),
        color=alt.condition("datum.macd_hist >= 0", alt.value("#26a69a"), alt.value("#ef5350")),
    )
    l1 = base.mark_line(color="#4c9aff").encode(y="macd:Q")
    l2 = base.mark_line(color="#f6c343").encode(y="macd_signal:Q")
    return (hist + l1 + l2).properties(height=160)


EXPORT_COLUMNS = {
    "symbol": "Coppia", "price": "Prezzo", "change_24h": "Var. 24h %", "volume_24h": "Volume 24h",
    "rsi": "RSI", "trend_ema": "Trend EMA", "vol_ratio": "Volume rel.", "volatility": "Volatilità ATR %",
    "score": "Punteggio", "signal": "Segnale", "alerts": "Allerte",
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


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.8f}".rstrip("0")


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
            "symbol", "price", "change_24h", "spark", "score", "signal", "rsi",
            "trend_ema", "vol_ratio", "volatility", "volume_24h", "alerts",
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
    st.altair_chart(price_chart(df, symbol), width="stretch")
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
