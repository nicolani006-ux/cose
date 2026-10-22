"""Dati crypto senza interfaccia: exchange, coin più scambiate e scansione con punteggio,
livelli BUY/SELL/STOP e statistica storica. Usato dall'app, dal bot e da excel_previsioni.py."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import ccxt
import pandas as pd

from indicators import HORIZON_BARS, analyze, plan_and_stats

OHLCV_LIMIT = 1000  # candele scaricate: servono anche per la statistica storica dei segnali

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD",
    "EUR", "EURT", "EURC", "EURI", "GBP", "USD", "USDE", "USD1", "RLUSD", "UST",
    "XUSD", "BFUSD", "AEUR", "WBTC", "WETH", "PAXG", "XAUT",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S", "2L", "2S")


def is_tradeable_base(base: str) -> bool:
    if base in STABLECOINS:
        return False
    return not any(base.endswith(s) and len(base) > len(s) for s in LEVERAGED_SUFFIXES)


_EXCHANGES: dict = {}
_EX_LOCK = threading.Lock()


def get_exchange(exchange_id: str):
    """Exchange ccxt con i mercati già caricati (uno per processo)."""
    cls = getattr(ccxt, exchange_id)
    with _EX_LOCK:
        ex = _EXCHANGES.get((exchange_id, cls))
        if ex is None:
            params = {"enableRateLimit": False, "timeout": 15000}
            if exchange_id == "binance":  # solo mercati spot: più veloce e basta api.binance.com
                params["options"] = {"fetchMarkets": {"types": ["spot"]}}
            ex = cls(params)
            ex.load_markets()
            _EXCHANGES[(exchange_id, cls)] = ex
    return ex


def ohlcv_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop(columns="ts").dropna()


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


def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = OHLCV_LIMIT) -> pd.DataFrame:
    ex = get_exchange(exchange_id)
    return ohlcv_to_df(ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit))


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
            return {**row, **analyze(df), **plan_and_stats(df, horizon=HORIZON_BARS.get(timeframe, 48)),
                    "last_candle": int(df["time"].iloc[-1].value // 1_000_000)}, None
        except Exception as e:  # noqa: BLE001 - un simbolo non deve bloccare la scansione
            return None, f"{row['symbol']}: {e}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(work, tops.to_dict("records")))
    errors = [err for _, err in outcomes if err]
    out = pd.DataFrame([res for res, _ in outcomes if res])
    if not out.empty:
        out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out, errors
