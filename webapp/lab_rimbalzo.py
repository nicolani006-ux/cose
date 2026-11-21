"""Strategia "rimbalzo": candidati per il Laboratorio (`collect`) e segnale dal vivo per il bot (`plan`).

Il Laboratorio non la applica mai da solo: si sceglie a mano (impostazioni, strategia = rimbalzo).

Segnale a candela chiusa: il prezzo chiude sotto la banda di Bollinger bassa (media 20 - k deviazioni),
l'RSI è sotto RIMB_RSI e BTC è sopra la sua EMA 200 sullo stesso timeframe (mercato non in discesa).
Piano compatibile con il bot: BUY subito (limite al prezzo + DIP_ENTRY_BUFFER%), SELL fisso alla media a 20
del momento del segnale, STOP a multipli di ATR, vendita al mercato dopo `horizon` candele.

Ricerca del 16/11/2026 su 1 anno di candele 1h (58 coin USDC su Bybit EU): l'unica famiglia, tra breakout,
forza relativa, incrocio di medie, compra il ribasso e rimbalzo, positiva nella maggior parte dei periodi
e non dovuta a 2-3 coin fortunate. Il vantaggio è piccolo e sensibile a commissioni + spread: va provata in
simulazione sui dati nuovi prima dei soldi veri.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

log = logging.getLogger("radar.web.lab")

KIND = "rimbalzo"
TIMEFRAMES = ("1h",)
RIMB_BANDS = (3.0, 3.5)       # deviazioni standard sotto la media a 20
RIMB_STOPS = (1.5, 2.0)       # STOP in multipli di ATR sotto il prezzo
RIMB_HORIZONS = (24, 80)      # candele massime, poi vendita al mercato
RIMB_RSI = 35.0
REGIME_EMA = 200              # BTC sopra la sua EMA 200 dello stesso timeframe
MIN_GAIN_PCT = 0.3            # la media deve stare almeno lo 0,3% sopra il BUY (sennò le commissioni la mangiano)


def rimb_id(tf: str, band: float, ks: float, hz: int) -> str:
    return f"rimb|{tf}|{float(band):g}|{float(ks):g}|{int(hz)}"


def btc_regime(btc: pd.DataFrame | None) -> pd.Series:
    """time -> BTC sopra la sua EMA REGIME_EMA (a candela chiusa). Serie vuota se BTC non c'è."""
    if btc is None or len(btc) <= REGIME_EMA:
        return pd.Series(dtype=bool)
    c = btc["close"].astype(float)
    up = (c > c.ewm(span=REGIME_EMA, adjust=False).mean()).to_numpy(copy=True)
    up[:REGIME_EMA] = False
    return pd.Series(up, index=pd.DatetimeIndex(btc["time"]))


def signals(df: pd.DataFrame, d: pd.DataFrame, band: float, regime: pd.Series) -> np.ndarray:
    """Indici delle candele in cui scatta il segnale (prima candela sotto la banda, non le successive)."""
    c = d["close"].to_numpy(dtype=float)
    low_band = (d["bb_mid"] - band * d["close"].rolling(20).std()).to_numpy()
    rsi = d["rsi"].to_numpy(dtype=float)
    up = regime.reindex(pd.DatetimeIndex(df["time"])).fillna(False).to_numpy(dtype=bool) if len(regime) else \
        np.zeros(len(df), dtype=bool)
    cond = np.array((c < low_band) & (rsi < RIMB_RSI) & up, dtype=bool)
    cond[:200] = False
    ok = np.zeros(len(df), dtype=bool)
    ok[1:] = cond[1:] & ~cond[:-1]
    return np.flatnonzero(ok)


def plan(df: pd.DataFrame, regime: pd.Series, band: float, k_stop: float, d: pd.DataFrame | None = None) -> dict:
    """Segnale e piano sull'ultima candela chiusa di `df`, con gli stessi nomi della strategia dip (dip_hit,
    dip_buy, dip_sell, dip_stop, ...) così il motore e il bot li usano allo stesso modo. Stessa regola del
    Laboratorio: `signals` sull'intera serie, il segnale vale solo se cade sull'ultima candela."""
    from indicators import DIP_ENTRY_BUFFER, add_indicators

    if df is None or len(df) < 221:
        return {"dip_hit": False}
    d = add_indicators(df) if d is None else d
    last = len(df) - 1
    c, a, mid = (float(d[k].iloc[-1]) for k in ("close", "atr", "bb_mid"))
    low_band = mid - band * float(d["close"].iloc[-20:].std())
    out = {"dip_hit": False, "rimb_band_low": low_band, "rimb_mid": mid, "atr": a}
    if not (np.isfinite(a) and a > 0 and np.isfinite(mid) and c > 0):
        return out
    buy, sell, stop = c * (1 + DIP_ENTRY_BUFFER / 100), mid, max(c - k_stop * a, 0.0)
    out.update(dip_buy=buy, dip_sell=sell, dip_stop=stop, dip_upside_pct=(sell / buy - 1) * 100,
               dip_risk_pct=(1 - stop / buy) * 100)
    hits = signals(df, d, band, regime)
    out["dip_hit"] = bool(len(hits) and hits[-1] == last and sell >= buy * (1 + MIN_GAIN_PCT / 100) and stop > 0)
    return out


def collect(exchange: str, quote: str, timeframe: str, n_coins: int, fee_pct: float, fetch=None, tops=None) -> dict:
    """Candidati per ogni combinazione (banda, STOP, durata): id -> (tf, parametri, lista), come collect_dip."""
    import crypto_core
    from indicators import DIP_ENTRY_BUFFER, add_indicators, simulate_trade
    from webapp.lab import DIP_HISTORY, FETCH_WORKERS, STOP_SLIPPAGE_PCT, history

    if timeframe not in TIMEFRAMES:
        return {}
    fetch = fetch or (lambda sym: history(exchange, sym, timeframe, DIP_HISTORY.get(timeframe, 1000)))
    symbols = tops if tops is not None else list(crypto_core.top_symbols(exchange, quote, n_coins)["symbol"])
    grid = [(b, ks, hz) for b in RIMB_BANDS for ks in RIMB_STOPS for hz in RIMB_HORIZONS]
    out: dict = {rimb_id(timeframe, *g): (timeframe, g, []) for g in grid}

    def load(sym):
        try:
            return sym, fetch(sym)
        except Exception as e:  # noqa: BLE001 - una coin non deve fermare il laboratorio
            log.info("laboratorio: %s non scaricata: %s", sym, e)
            return sym, None

    btc_sym = f"BTC/{quote}"
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        loaded = dict(pool.map(load, list(dict.fromkeys([btc_sym, *symbols]))))
    regime = btc_regime(loaded.get(btc_sym))
    if not len(regime):
        log.info("laboratorio: rimbalzo saltato, mancano le candele di %s", btc_sym)
        return out
    for sym in symbols:
        df = loaded.get(sym)
        if df is None or len(df) < 300:
            continue
        d = add_indicators(df)
        o, h, lo, c, a, mid = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "atr", "bb_mid"))
        times = np.array([ts.value // 1_000_000 for ts in df["time"]], dtype=np.int64)
        for band in RIMB_BANDS:
            for i in signals(df, d, band, regime):
                if i >= len(df) - 1 or not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(mid[i])):
                    continue
                buy, sell = c[i] * (1 + DIP_ENTRY_BUFFER / 100), mid[i]
                if sell < buy * (1 + MIN_GAIN_PCT / 100):
                    continue
                for (b2, ks, hz) in grid:
                    if b2 != band:
                        continue
                    stop = c[i] - ks * a[i]
                    if stop <= 0:
                        continue
                    sim = simulate_trade(o, h, lo, c, i, buy, stop, sell, hz)
                    st = sim["status"]
                    if st in ("pending", "open"):
                        continue  # ancora in corso a fine dati
                    row = {"symbol": sym, "t": int(times[i]), "score": 0.0, "prev": 0.0, "prob": 0.0,
                           "buy": buy, "sell": sell, "stop": stop, "status": st}
                    if st != "unfilled":
                        entry, exit_px = sim["entry"], sim["exit_price"]
                        if st == "loss":
                            exit_px *= 1 - STOP_SLIPPAGE_PCT / 100
                        row.update(entry=entry, exit=exit_px, t_in=int(times[sim["fill_bar"]]),
                                   t_out=int(times[sim["exit_bar"]]),
                                   ret=(exit_px * (1 - fee_pct / 100)) / (entry * (1 + fee_pct / 100)) * 100 - 100)
                    out[rimb_id(timeframe, b2, ks, hz)][2].append(row)
    for v in out.values():
        v[2].sort(key=lambda r: (r["t"], r["symbol"]))
    return out
