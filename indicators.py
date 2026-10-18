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
