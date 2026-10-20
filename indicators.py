"""Indicatori tecnici e punteggio di trend condivisi da crypto e azioni."""

from __future__ import annotations

import numpy as np
import pandas as pd


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


# --------------------------------------------------------------------------- #
# Punteggio candela per candela, piano BUY / SELL / STOP e statistica storica
# --------------------------------------------------------------------------- #

def score_series(df: pd.DataFrame) -> pd.Series:
    """Lo stesso punteggio di `analyze`, calcolato per ogni candela usando solo i dati
    disponibili fino a quella candela (serve per la statistica storica)."""
    d = add_indicators(df).reset_index(drop=True)
    n = len(d)
    close = d["close"]
    s = np.where(close > d["ema20"], 15.0, -15.0)
    s += np.where(d["ema20"] > d["ema50"], 15.0, -15.0)
    s += np.where(np.arange(n) >= 199, np.where(d["ema50"] > d["ema200"], 20.0, -20.0), 0.0)
    s += np.where(d["macd_hist"] > 0, 10.0, -10.0)

    sign = np.sign((d["macd"] - d["macd_signal"]).to_numpy())
    cross = np.zeros(n)
    decided = np.zeros(n, dtype=bool)
    for k in (2, 1, 0):  # come _crossed: vince l'incrocio più vecchio nelle ultime 3 candele
        prev = np.roll(sign, k + 1)
        cur = np.roll(sign, k)
        up = (prev <= 0) & (cur > 0)
        dn = (prev >= 0) & (cur < 0)
        valid = np.arange(n) >= 3
        new_up = up & ~decided & valid
        new_dn = dn & ~decided & ~new_up & valid
        cross[new_up], cross[new_dn] = 1, -1
        decided |= new_up | new_dn
    s += 10 * cross

    r = d["rsi"].fillna(50).to_numpy()
    s += np.select([(r >= 55) & (r < 70), r >= 70, (r > 30) & (r <= 45), r <= 30], [15, 5, -15, -5], 0)

    vol_avg = d["volume"].rolling(20, min_periods=1).mean().shift(1)
    ratio = np.where(vol_avg > 0, d["volume"] / vol_avg, 1.0)
    s = np.where(ratio >= 1.5, s * 1.2, s)

    hi20 = d["high"].rolling(20, min_periods=1).max().shift(1)
    lo20 = d["low"].rolling(20, min_periods=1).min().shift(1)
    s += np.where(close > hi20, 10, np.where(close < lo20, -10, 0))
    return pd.Series(np.clip(s, -100, 100), index=df.index)


# Distanze in multipli di ATR (volatilità media) e durata massima di un'operazione, per timeframe.
PLAN_ENTRY_ATR = 0.5     # BUY: piccolo ritracciamento sotto il prezzo attuale
PLAN_STOP_ATR = 1.5      # STOP: 1,5 ATR sotto il BUY
PLAN_TARGET_ATR = 3.0    # SELL: 3 ATR sopra il BUY  (rendimento/rischio = 2)
PLAN_FILL_BARS = 5       # quante candele resta valido l'ordine di acquisto
HORIZON_BARS = {"15m": 96, "1h": 48, "4h": 30, "1d": 20}


def trade_plan(df: pd.DataFrame) -> dict:
    """Prezzi suggeriti: BUY (acquisto con ordine limite), SELL (obiettivo), STOP (vendi se scende sotto)."""
    d = add_indicators(df)
    last = d.iloc[-1]
    close, a = float(last["close"]), float(last["atr"]) if pd.notna(last["atr"]) else np.nan
    if not np.isfinite(a) or a <= 0:
        return {"buy": np.nan, "sell": np.nan, "stop": np.nan, "upside_pct": np.nan, "risk_pct": np.nan,
                "support": np.nan, "resistance": np.nan}
    buy = close - PLAN_ENTRY_ATR * a
    stop = buy - PLAN_STOP_ATR * a
    sell = buy + PLAN_TARGET_ATR * a
    recent = d.tail(60)
    return {
        "buy": buy, "sell": sell, "stop": max(stop, 0.0),
        "upside_pct": (sell / buy - 1) * 100, "risk_pct": (1 - stop / buy) * 100,
        "support": float(recent["low"].min()), "resistance": float(recent["high"].max()),
    }


def backtest_plan(df: pd.DataFrame, horizon: int = 20, threshold: float = 20) -> dict:
    """Applica il piano a tutti i segnali d'acquisto passati della serie.

    Segnale = il punteggio supera `threshold`. Si piazza un ordine BUY (valido
    PLAN_FILL_BARS candele); se eseguito, l'operazione finisce al primo tra SELL
    (vinta), STOP (persa) o dopo `horizon` candele (chiusa al prezzo di quel momento).
    Se nella stessa candela si toccano STOP e SELL, si considera persa (stima prudente)."""
    d = add_indicators(df).reset_index(drop=True)
    sc = score_series(df).to_numpy()
    o, h, lo, c, a = (d[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    n = len(d)
    wins = losses = timeouts = signals = 0
    returns, bars = [], []
    t, busy_until = 60, -1
    while t < n - 1:
        if not (sc[t] >= threshold and sc[t - 1] < threshold) or t <= busy_until or not np.isfinite(a[t]):
            t += 1
            continue
        signals += 1
        buy = c[t] - PLAN_ENTRY_ATR * a[t]
        stop, target = buy - PLAN_STOP_ATR * a[t], buy + PLAN_TARGET_ATR * a[t]
        fill = next((j for j in range(t + 1, min(t + 1 + PLAN_FILL_BARS, n)) if lo[j] <= buy), None)
        if fill is None:
            t += 1
            continue
        entry = min(buy, o[fill])
        end = fill + horizon - 1
        if end >= n:  # operazione ancora aperta: non si conta
            break
        outcome, exit_price, exit_bar = None, c[end], end
        for k in range(fill, end + 1):
            if lo[k] <= stop:
                outcome, exit_price, exit_bar = "loss", min(stop, o[k]) if k > fill else stop, k
                break
            if k > fill and h[k] >= target:
                outcome, exit_price, exit_bar = "win", max(target, o[k]), k
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            timeouts += 1
        returns.append((exit_price / entry - 1) * 100)
        bars.append(exit_bar - fill + 1)
        busy_until = exit_bar
        t = exit_bar + 1
    trades = wins + losses + timeouts
    return {
        "bt_signals": signals, "bt_trades": trades, "bt_wins": wins, "bt_losses": losses, "bt_timeouts": timeouts,
        "bt_win_rate": wins / trades * 100 if trades else np.nan,
        "bt_fill_rate": trades / signals * 100 if signals else np.nan,
        "bt_avg_ret": float(np.mean(returns)) if returns else np.nan,
        "bt_avg_bars": float(np.mean(bars)) if bars else np.nan,
    }


def plan_and_stats(df: pd.DataFrame, horizon: int = 20) -> dict:
    return {**trade_plan(df), **backtest_plan(df, horizon=horizon)}


def stat_text(row, unit: str = "candele") -> str:
    """Frase breve sulla statistica storica di un titolo."""
    n = row.get("bt_trades", 0) or 0
    if not n:
        return "nessun segnale storico sufficiente"
    txt = (f"{row['bt_win_rate']:.0f}% dei {int(n)} acquisti passati ha raggiunto il SELL prima dello STOP "
           f"(rendimento medio {row['bt_avg_ret']:+.1f}% in {row['bt_avg_bars']:.0f} {unit})")
    fill = row.get("bt_fill_rate")
    if fill is not None and not pd.isna(fill) and fill < 100:
        txt += f"; l'ordine BUY è stato eseguito nel {fill:.0f}% dei segnali"
    if n < 5:
        txt += " ⚠️ campione piccolo, poco affidabile"
    return txt


def overall_stats(rows: pd.DataFrame) -> dict | None:
    """Statistica complessiva su tutti i titoli/coin: % di segnali arrivati al SELL e rendimento medio."""
    if rows.empty or "bt_trades" not in rows:
        return None
    trades = rows["bt_trades"].fillna(0)
    total = int(trades.sum())
    if not total:
        return None
    return {"trades": total, "win_rate": rows["bt_wins"].fillna(0).sum() / total * 100,
            "avg_ret": float((rows["bt_avg_ret"].fillna(0) * trades).sum() / total)}


PLAN_HELP = ("BUY = ordine d'acquisto (limite) mezzo ATR sotto il prezzo attuale, valido 5 candele · SELL = obiettivo 3 ATR sopra il BUY · "
             "STOP = vendi se scende 1,5 ATR sotto il BUY (rendimento/rischio 2:1). ATR = movimento medio di una candela. "
             "Successo storico = % dei segnali d'acquisto passati di questo titolo in cui, con questi livelli, "
             "il prezzo ha toccato il SELL prima dello STOP.")
