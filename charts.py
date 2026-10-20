"""Grafici Altair condivisi (candele + medie mobili, RSI, MACD)."""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd

LEVEL_COLORS = {"BUY": "#2ecc71", "SELL": "#00bcd4", "STOP": "#ff5252"}


def price_chart(df: pd.DataFrame, symbol: str, levels: dict | None = None) -> alt.Chart:
    """Candele con medie mobili; `levels` (es. {"BUY": 10, "SELL": 12, "STOP": 9}) aggiunge linee orizzontali."""
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
    layers = [wick, body, lines]
    lv = pd.DataFrame([{"livello": k, "prezzo": float(v), "label": f"{k} {fmt_price(float(v))}"}
                       for k, v in (levels or {}).items() if v is not None and np.isfinite(v) and v > 0])
    if not lv.empty:
        lcolor = alt.Color("livello:N", scale=alt.Scale(domain=list(LEVEL_COLORS), range=list(LEVEL_COLORS.values())),
                           legend=None)
        layers.append(alt.Chart(lv).mark_rule(strokeDash=[6, 4], strokeWidth=1.5).encode(y="prezzo:Q", color=lcolor))
        layers.append(alt.Chart(lv).mark_text(align="left", dx=4, dy=-7, fontWeight="bold").encode(
            y="prezzo:Q", x=alt.value(0), text="label:N", color=lcolor))
    return alt.layer(*layers).resolve_scale(color="independent").properties(height=420)


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


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.8f}".rstrip("0")
