"""Elementi d'interfaccia condivisi da Crypto e Azioni."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from charts import fmt_price
from indicators import PLAN_HELP, stat_text


def plan_metrics(row, unit: str = "candele", fmt=fmt_price) -> None:
    """Riga con i prezzi BUY / SELL / STOP e la statistica storica."""
    if pd.isna(row["buy"]):
        st.caption("Storico insufficiente per calcolare i livelli.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Prezzo BUY", fmt(row["buy"]), help=PLAN_HELP, border=True)
    c2.metric("🎯 Prezzo SELL", fmt(row["sell"]), delta=f"+{row['upside_pct']:.1f}%", help=PLAN_HELP, border=True)
    c3.metric("🛑 STOP loss", fmt(row["stop"]), delta=f"-{row['risk_pct']:.1f}%", help=PLAN_HELP, border=True)
    wr = row["bt_win_rate"]
    c4.metric("📊 Successo storico", "n/d" if pd.isna(wr) else f"{wr:.0f}%",
              delta=f"su {int(row['bt_trades'])} operazioni" if row["bt_trades"] else None, delta_color="off",
              delta_arrow="off",
              help=PLAN_HELP, border=True)
    st.caption("📊 " + stat_text(row, unit))
    if row["score"] < 20:
        st.caption("⚠️ Il segnale attuale non è d'acquisto: lo STOP serve se possiedi già il titolo, "
                   "il BUY come riferimento per un ingresso futuro.")
