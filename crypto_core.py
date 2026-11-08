"""Dati crypto senza interfaccia: exchange, coin più scambiate e scansione con punteggio,
livelli BUY/SELL/STOP e statistica storica. Usato dall'app, dal bot e da excel_previsioni.py.

Ottimizzazioni:
- si usano solo **candele chiuse**: il segnale mostrato è lo stesso che la statistica storica
  verifica sul passato e non cambia finché la candela non si chiude;
- le candele vengono scaricate per intero solo la prima volta, poi solo quelle nuove;
- statistica storica e Time Machine si ricalcolano solo quando si chiude una nuova candela;
- limite di richieste dell'exchange rispettato, con nuovi tentativi su errori temporanei.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import ccxt
import pandas as pd

from indicators import HORIZON_BARS, add_indicators, analyze, plan_and_stats

OHLCV_LIMIT = 1000  # candele scaricate: servono anche per la statistica storica dei segnali
SCAN_WORKERS = 8
RETRIES = 3

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP", "USDD", "PYUSD",
    "EUR", "EURT", "EURC", "EURI", "GBP", "USD", "USDE", "USD1", "RLUSD", "UST",
    "XUSD", "BFUSD", "AEUR", "WBTC", "WETH", "PAXG", "XAUT", "U", "USDS", "USDG", "USD0", "FRAX", "LUSD",
    "GHO", "SUSD", "USDX", "USDQ", "USDR", "EURR", "BNSOL", "WBETH", "STETH",
}
MIN_VOLATILITY_PCT = 0.1  # sotto questa volatilità media per candela è una stablecoin o un token "ancorato"
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S", "2L", "2S")


def is_tradeable_base(base: str) -> bool:
    if base in STABLECOINS:
        return False
    return not any(base.endswith(s) and len(base) > len(s) for s in LEVERAGED_SUFFIXES)


def timeframe_ms(timeframe: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return int(timeframe[:-1] or 1) * units[timeframe[-1]]


def drop_open_candle(rows: list, timeframe: str, now_ms: int | None = None) -> list:
    """Toglie l'ultima candela se non è ancora chiusa (l'exchange restituisce anche quella in corso)."""
    if not rows:
        return rows
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return rows[:-1] if rows[-1][0] + timeframe_ms(timeframe) > now_ms else rows


# Server ufficiale di Binance per i soli dati pubblici di mercato: risponde anche dove api.binance.com
# è bloccato per area geografica (es. server cloud negli USA). Se non raggiungibile si usa api.binance.com.
BINANCE_DATA_API = "https://data-api.binance.vision/api/v3"

_EXCHANGES: dict = {}
_EX_LOCK = threading.Lock()


def get_exchange(exchange_id: str):
    """Exchange ccxt con i mercati già caricati (uno per processo)."""
    cls = getattr(ccxt, exchange_id)
    with _EX_LOCK:
        ex = _EXCHANGES.get((exchange_id, cls))
        if ex is None:
            # enableRateLimit: ccxt distanzia le richieste secondo i limiti dell'exchange (anche tra thread),
            # così una scansione ripetuta non rischia errori 429 o il blocco temporaneo dell'IP.
            params = {"enableRateLimit": True, "timeout": 15000}
            if exchange_id == "binance":  # solo mercati spot: più veloce
                params["options"] = {"fetchMarkets": {"types": ["spot"]}}
                ex = _binance_public_data(cls, params)
            else:
                ex = _new(cls, params)
                _retry(ex.load_markets)
            _EXCHANGES[(exchange_id, cls)] = ex
    return ex


def _new(cls, params: dict):
    ex = cls(params)
    session = getattr(ex, "session", None)
    if session is not None:  # usa il proxy di sistema se configurato (ccxt di norma lo ignora)
        session.trust_env = True
    return ex


def _binance_public_data(cls, params: dict):
    ex = _new(cls, params)
    try:
        ex.urls["api"]["public"] = BINANCE_DATA_API
        _retry(ex.load_markets)
        return ex
    except Exception:  # noqa: BLE001 - dominio non raggiungibile: si prova l'indirizzo principale
        ex = _new(cls, params)
        _retry(ex.load_markets)
        return ex


_TEMPORARY = tuple(getattr(ccxt, n) for n in ("NetworkError", "RateLimitExceeded", "DDoSProtection",
                                              "ExchangeNotAvailable", "RequestTimeout") if hasattr(ccxt, n))


def _retry(fn, *args, retries: int = RETRIES, pause: float = 1.0, **kwargs):
    """Riprova le chiamate all'exchange sugli errori temporanei (rete, limite di richieste), con attesa crescente."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except _TEMPORARY:
            if attempt == retries - 1:
                raise
            time.sleep(pause * 2 ** attempt)


def ohlcv_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop(columns="ts").dropna()


def top_symbols(exchange_id: str, quote: str, n: int) -> pd.DataFrame:
    ex = get_exchange(exchange_id)
    tickers = _retry(ex.fetch_tickers)
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


# --------------------------------------------------------------------------- #
# Candele con download incrementale
# --------------------------------------------------------------------------- #

_CANDLES: dict = {}            # (oggetto exchange, simbolo, timeframe) -> righe [ts, o, h, l, c, v] (anche quella in corso)
_CANDLES_LOCK = threading.Lock()
REFRESH_FULL_AFTER = 50        # se mancano più candele di così, si riscarica tutto da capo


def _raw_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = OHLCV_LIMIT) -> list:
    """Ultime `limit` candele: la prima volta tutte, poi solo le nuove (più l'ultima, che può essere cambiata)."""
    ex = get_exchange(exchange_id)
    key = (id(ex), symbol, timeframe)
    with _CANDLES_LOCK:
        cached = _CANDLES.get(key)
    tf = timeframe_ms(timeframe)
    if cached and len(cached) >= limit and (time.time() * 1000 - cached[-1][0]) / tf < REFRESH_FULL_AFTER:
        try:
            new = _retry(ex.fetch_ohlcv, symbol, timeframe=timeframe, since=int(cached[-1][0]), limit=REFRESH_FULL_AFTER + 5)
        except TypeError:  # exchange (o finto exchange dei test) senza il parametro since
            new = None
        if new:
            merged = {int(r[0]): r for r in cached}
            merged.update({int(r[0]): r for r in new})
            rows = [merged[k] for k in sorted(merged)][-limit:]
            with _CANDLES_LOCK:
                _CANDLES[key] = rows
            return rows
    rows = _retry(ex.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit)
    with _CANDLES_LOCK:
        _CANDLES[key] = rows
    return rows


def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = OHLCV_LIMIT,
                closed_only: bool = True) -> pd.DataFrame:
    rows = _raw_ohlcv(exchange_id, symbol, timeframe, limit)
    return ohlcv_to_df(drop_open_candle(rows, timeframe) if closed_only else rows)


# --------------------------------------------------------------------------- #
# Scansione
# --------------------------------------------------------------------------- #

_RESULTS: OrderedDict = OrderedDict()  # (exchange, simbolo, timeframe, ultima candela chiusa) -> risultati
_RESULTS_MAX = 2000
_RESULTS_LOCK = threading.Lock()


def analyze_symbol(exchange_id: str, symbol: str, timeframe: str, df: pd.DataFrame) -> dict | None:
    """Punteggio + piano + statistica per una coin. Il risultato dipende solo dalle candele chiuse,
    quindi resta valido (e viene riusato) finché non se ne chiude un'altra."""
    if len(df) < 60:
        return None
    last = int(df["time"].iloc[-1].value // 1_000_000)
    key = (exchange_id, symbol, timeframe, last, len(df), float(df["close"].iloc[-1]), float(df["close"].iloc[0]))
    with _RESULTS_LOCK:
        if key in _RESULTS:
            _RESULTS.move_to_end(key)
            return _RESULTS[key]
    d = add_indicators(df)
    info = analyze(df, d)
    if not info["volatility"] >= MIN_VOLATILITY_PCT:  # prezzo praticamente fermo (stablecoin)
        res = None
    else:
        res = {**info, **plan_and_stats(df, horizon=HORIZON_BARS.get(timeframe, 48), d=d), "last_candle": last}
    with _RESULTS_LOCK:
        _RESULTS[key] = res
        while len(_RESULTS) > _RESULTS_MAX:
            _RESULTS.popitem(last=False)
    return res


def scan(exchange_id: str, quote: str, n: int, timeframe: str) -> tuple[pd.DataFrame, list[str]]:
    tops = top_symbols(exchange_id, quote, n)
    if tops.empty:
        return tops, []

    def work(row):
        try:
            df = fetch_ohlcv(exchange_id, row["symbol"], timeframe)
            res = analyze_symbol(exchange_id, row["symbol"], timeframe, df)
            return ({**row, **res} if res else None), None
        except Exception as e:  # noqa: BLE001 - un simbolo non deve bloccare la scansione
            return None, f"{row['symbol']}: {e}"

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        outcomes = list(pool.map(work, tops.to_dict("records")))
    errors = [err for _, err in outcomes if err]
    out = pd.DataFrame([res for res, _ in outcomes if res])
    if not out.empty:
        out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out, errors
