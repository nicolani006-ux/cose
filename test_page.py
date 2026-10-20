"""Pagina 🧪 Test previsioni: risultati reali delle previsioni crypto e file Excel."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import previsioni as pv
from stocks import background
from stocks.config import load_config


def main() -> None:
    st.set_page_config(page_title="Test previsioni", page_icon="🧪", layout="wide")
    st.title("🧪 Test delle previsioni crypto")
    st.caption("Ad ogni controllo orario il bot registra le crypto con segnale d'acquisto (probabilità, BUY, SELL, "
               "margine ipotizzato) e poi le segue sui prezzi reali: guadagno effettivo se avessi comprato al BUY e "
               "venduto al SELL (o allo STOP / a scadenza) quando te l'ha indicato.")
    cfg = load_config()
    a, b = st.columns([1, 3])
    if a.button("▶️ Aggiorna adesso", type="primary"):
        with st.spinner("Aggiorno le previsioni sui prezzi reali…"):
            try:
                res = pv.run(cfg)
                st.success(f"{len(res['new'])} nuove previsioni, {len(res['closed'])} operazioni chiuse.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Aggiornamento non riuscito: {e}")
    b.write("Bot: " + ("🟢 in esecuzione – il test avanza da solo ogni ora" if background.is_running()
                       else "🔴 fermo – avvialo da ⚙️ Impostazioni & Bot perché il test avanzi da solo"))

    journal = pv.load_journal()
    s = pv.compute_stats(journal)
    if not journal:
        st.info("Nessuna previsione ancora. Premi **Aggiorna adesso** o attendi il prossimo controllo del bot.")
        return

    c = st.columns(5)
    c[0].metric("Previsioni", s["total"], border=True)
    c[1].metric("Operazioni chiuse", s["closed"], delta=f"{s['open']} aperte · {s['pending']} in attesa",
                delta_color="off", delta_arrow="off", border=True)
    if s.get("closed"):
        c[2].metric("Successo reale", f"{s['win_rate']:.0f}%", delta=f"ipotizzato {s['prob_mean']:.0f}%",
                    delta_color="off", delta_arrow="off", border=True)
        c[3].metric("Guadagno medio netto", f"{s['net_mean']:+.2f}%", delta=f"ipotizzato +{s['margin_mean']:.2f}%",
                    delta_color="off", delta_arrow="off", border=True)
        c[4].metric(f"Con {pv.STAKE_EUR}€ a operazione", f"{s['total_eur']:+.2f} €", border=True)
    st.text(pv.summary_text(s))

    df = pv.to_frame(journal).sort_values("created", ascending=False)
    view = pd.DataFrame({
        "Crypto": df["symbol"],
        "Probabilità ipotizzata": df["probability"] * 100,
        "BUY → SELL": [f"{b:.8g} → {s_:.8g}" for b, s_ in zip(df["buy"], df["sell"])],
        "Margine ipotizzato %": df["expected_margin_pct"],
        "Guadagno effettivo %": df["ret_pct"].where(df["status"].isin(pv.CLOSED)),
        "Esito": df["status"].map(pv.STATUS_LABEL),
        "Data": df["created"].str[:16].str.replace("T", " "),
        "STOP": df["stop"],
        "Netto %": df["net_pct"].where(df["status"].isin(pv.CLOSED)),
        "Durata ore": df["hours"],
    })
    st.dataframe(view, hide_index=True, width="stretch", column_config={
        "Probabilità ipotizzata": st.column_config.NumberColumn(format="%.0f%%"),
        "Margine ipotizzato %": st.column_config.NumberColumn(format="+%.2f%%"),
        "Guadagno effettivo %": st.column_config.NumberColumn(format="%+.2f%%"),
        "Netto %": st.column_config.NumberColumn(format="%+.2f%%"),
        "STOP": st.column_config.NumberColumn(format="%.8g"),
        "Durata ore": st.column_config.NumberColumn(format="%.1f"),
    })

    closed = df[df["status"].isin(pv.CLOSED)].sort_values("closed_at")
    if not closed.empty:
        curve = pd.DataFrame({"Operazione": range(1, len(closed) + 1),
                              "€": (closed["net_pct"] * pv.STAKE_EUR / 100).cumsum().to_numpy()})
        st.altair_chart(alt.Chart(curve).mark_line(point=True).encode(x="Operazione:Q", y="€:Q").properties(
            title=f"Risultato cumulato con {pv.STAKE_EUR}€ per operazione", height=260), width="stretch")

    if not pv.EXCEL_FILE.exists():
        pv.write_excel(journal)
    st.download_button("📥 Scarica il file Excel", pv.EXCEL_FILE.read_bytes(), "test_previsioni.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption(f"Il file viene aggiornato ad ogni controllo: `{pv.EXCEL_FILE}`. "
               "Dal telefono scrivi /test al bot per riceverlo.")


if __name__ == "__main__":
    main()
