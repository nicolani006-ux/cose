"""Elementi d'interfaccia condivisi da Crypto e Azioni."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from charts import fmt_price
from indicators import PLAN_HELP, sizing_from_config, sizing_text, stat_text
from stocks.config import load_config


def plan_metrics(row, unit: str = "candele", fmt=fmt_price, fractional: bool = True) -> None:
    """Riga con i prezzi BUY / SELL / STOP e la statistica storica."""
    if pd.isna(row["buy"]):
        st.caption("Storico insufficiente per calcolare i livelli.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Prezzo BUY", fmt(row["buy"]), help=PLAN_HELP, border=True)
    c2.metric("🎯 Prezzo SELL", fmt(row["sell"]), delta=f"+{row['upside_pct']:.1f}%", help=PLAN_HELP, border=True)
    c3.metric("🛑 STOP loss", fmt(row["stop"]), delta=f"-{row['risk_pct']:.1f}%", help=PLAN_HELP, border=True)
    prob = row.get("probability")
    parts = []
    if not pd.isna(row.get("bt_win_rate")):
        parts.append(f"storico {row['bt_win_rate']:.0f}%")
    if not pd.isna(row.get("mc_win", np.nan)):
        parts.append(f"Time Machine {row['mc_win']:.0f}%")
    c4.metric("📊 Probabilità SELL", "n/d" if prob is None or pd.isna(prob) else f"{prob:.0f}%",
              delta=" · ".join(parts) or None, delta_color="off", delta_arrow="off", help=PLAN_HELP, border=True)
    st.caption("📊 " + stat_text(row, unit))
    if isinstance(row.get("mc_verdict"), str):
        st.caption(f"⏱️ Time Machine ({int(row['mc_replays'])} simulazioni): {row['mc_verdict']}; "
                   f"BUY eseguito nel {row['mc_fill_rate']:.0f}% dei casi, rendimento medio {row['mc_avg_ret']:+.2f}%")
    sz = sizing_from_config(row, load_config(), fractional=fractional)
    if sz:
        (st.success if sz["qty"] > 0 and row["score"] >= 20 else st.caption)(sizing_text(sz))
    if row["score"] < 20:
        st.caption("⚠️ Il segnale attuale non è d'acquisto: lo STOP serve se possiedi già il titolo, "
                   "il BUY come riferimento per un ingresso futuro.")
