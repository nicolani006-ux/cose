"""Stock Radar: classifica delle azioni, banche d'affari, Fed/BCE, SEC (8-K e 13F) e avvisi."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from charts import macd_chart, price_chart, rsi_chart
from indicators import add_indicators
from stocks import notify, scheduler
from stocks.analysis import macro_tone, score_stocks
from stocks.config import REPORT_DIR, UNIVERSE, load_config, save_config, ticker_info
from stocks.monitor import collect, run_once

TITLE = "Stock Radar – Azioni & Istituzioni"


@st.cache_data(ttl=900, show_spinner=False)
def load_snapshot(cfg_json: str):
    return collect(json.loads(cfg_json))


def _cfg_key(cfg: dict) -> str:
    keys = ("watchlist", "sec_user_agent", "controlla_13f", "controlla_8k")
    return json.dumps({k: cfg[k] for k in keys} | {"watchlist": sorted(cfg["watchlist"])}, sort_keys=True)


def header(snap, ranking: pd.DataFrame) -> None:
    tone = macro_tone(snap.news)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tono Fed", "🕊️ Tassi giù" if tone > 0.15 else "🦅 Tassi su" if tone < -0.15 else "Neutro",
              help="Calcolato dalle parole usate in comunicati, discorsi e audizioni della Fed degli ultimi 10 giorni.",
              border=True)
    c2.metric("Titoli con segnale d'acquisto", int((ranking["score"] >= 20).sum()), border=True)
    c3.metric("Titoli da evitare", int((ranking["score"] <= -20).sum()), border=True)
    top = ranking.iloc[0]
    c4.metric("Miglior candidato", f"{top['ticker']}", delta=f"{top['score']:+.0f}", border=True)


def tab_ranking(ranking: pd.DataFrame) -> None:
    sectors = sorted(ranking["sector"].unique())
    c1, c2 = st.columns([3, 1])
    chosen = c1.multiselect("Settori", sectors, default=sectors)
    min_score = c2.slider("Punteggio minimo", -100, 100, -100, step=10)
    view = ranking[ranking["sector"].isin(chosen) & (ranking["score"] >= min_score)]
    st.dataframe(
        view, hide_index=True, width="stretch", height=min(38 * (len(view) + 1), 900),
        column_order=["ticker", "name", "sector", "price", "chg_1d", "chg_1m", "spark", "score", "signal",
                      "s_tecnico", "s_analisti", "s_notizie", "s_istituzioni", "s_macro", "reasons"],
        column_config={
            "ticker": st.column_config.TextColumn("Titolo", pinned=True),
            "name": "Società", "sector": "Settore",
            "price": st.column_config.NumberColumn("Prezzo $", format="%.2f"),
            "chg_1d": st.column_config.NumberColumn("1 g", format="%+.2f%%"),
            "chg_1m": st.column_config.NumberColumn("1 mese", format="%+.1f%%"),
            "spark": st.column_config.LineChartColumn("3 mesi", width="small"),
            "score": st.column_config.ProgressColumn("Punteggio", min_value=-100, max_value=100, format="%+.0f"),
            "signal": "Segnale",
            "s_tecnico": st.column_config.NumberColumn("Tecnico", format="%+.0f"),
            "s_analisti": st.column_config.NumberColumn("Banche", format="%+.0f"),
            "s_notizie": st.column_config.NumberColumn("Notizie", format="%+.0f"),
            "s_istituzioni": st.column_config.NumberColumn("13F", format="%+.0f"),
            "s_macro": st.column_config.NumberColumn("Fed", format="%+.0f"),
            "reasons": st.column_config.TextColumn("Motivi", width="large"),
        },
    )
    st.caption("Punteggio = 35% analisi tecnica + 30% giudizi delle banche d'affari + 15% notizie "
               "+ 10% acquisti/vendite di BlackRock, Vanguard, State Street (13F) + 10% tono della Fed sul settore.")
    buf = io.BytesIO()
    view.drop(columns=["spark"]).to_excel(buf, index=False, engine="openpyxl")
    st.download_button("📥 Scarica Excel", buf.getvalue(), f"stock_radar_{datetime.now():%Y%m%d_%H%M}.xlsx")


def tab_detail(snap, ranking: pd.DataFrame) -> None:
    t = st.selectbox("Titolo", ranking["ticker"], format_func=lambda x: f"{x} – {ticker_info(x)[0]}")
    row = ranking.set_index("ticker").loc[t]
    c1, c2, c3 = st.columns(3)
    c1.metric("Prezzo", f"{row['price']:,.2f} $", delta=f"{row['chg_1d']:+.2f}% oggi")
    c2.metric("Punteggio", f"{row['score']:+.0f}", delta=row["signal"], delta_color="off", delta_arrow="off")
    c3.metric("Settore", row["sector"])
    st.info("**Motivi:** " + row["reasons"])
    df = add_indicators(snap.prices[t])
    st.altair_chart(price_chart(df, t), width="stretch")
    a, b = st.columns(2)
    a.altair_chart(rsi_chart(df), width="stretch")
    b.altair_chart(macd_chart(df), width="stretch")

    r = snap.ratings[snap.ratings["ticker"] == t] if not snap.ratings.empty else pd.DataFrame()
    st.subheader("Giudizi delle banche")
    if r.empty:
        st.caption("Nessun giudizio recente dalle banche monitorate.")
    else:
        st.dataframe(r[["date", "bank", "action", "from_grade", "to_grade", "pt_prev", "pt_now"]], hide_index=True)
    st.subheader("Notizie")
    news = snap.news[snap.news["tickers"].map(lambda x: t in x)] if not snap.news.empty else pd.DataFrame()
    _news_list(news.head(20))


def _news_list(news: pd.DataFrame) -> None:
    if news.empty:
        st.caption("Nessuna notizia.")
        return
    for n in news.itertuples():
        when = n.published.tz_convert(None).strftime("%d/%m %H:%M") if pd.notna(n.published) else ""
        mood = "📈" if n.sentiment >= 0.34 else "📉" if n.sentiment <= -0.34 else "▫️"
        tone = ""
        if n.tone is not None and pd.notna(n.tone) and n.tone != 0:
            tone = " · 🕊️ accomodante" if n.tone > 0 else " · 🦅 restrittivo"
        tick = f" · {', '.join(n.tickers)}" if n.tickers else ""
        st.markdown(f"{mood} **[{n.title}]({n.link})**  \n<small>{n.group} · {n.publisher} · {when} UTC{tone}{tick}</small>",
                    unsafe_allow_html=True)


def tab_institutions(snap) -> None:
    if snap.news.empty:
        st.warning("Nessuna notizia scaricata.")
        return
    groups = list(dict.fromkeys(snap.news["group"]))
    chosen = st.multiselect("Fonti", groups, default=groups)
    news = snap.news[snap.news["group"].isin(chosen)].sort_values("published", ascending=False, na_position="last")
    _news_list(news.head(80))


def tab_analysts(snap) -> None:
    if snap.ratings.empty:
        st.info("Nessun giudizio di Goldman Sachs, J.P. Morgan, Morgan Stanley, Bank of America "
                "(o altre grandi banche) sui titoli della watchlist negli ultimi 45 giorni.")
        return
    st.dataframe(
        snap.ratings.drop(columns=["weight"]), hide_index=True, width="stretch",
        column_config={"date": st.column_config.DatetimeColumn("Data", format="DD/MM/YYYY"),
                       "ticker": "Titolo", "bank": "Banca", "firm": "Nome su Yahoo", "action": "Azione",
                       "from_grade": "Da", "to_grade": "A",
                       "pt_prev": st.column_config.NumberColumn("Target prec.", format="%.0f $"),
                       "pt_now": st.column_config.NumberColumn("Target", format="%.0f $")},
    )
    st.caption("Azione: up = rialzo del giudizio, down = ribasso, init = inizio copertura, main/reit = conferma.")


def tab_sec(snap) -> None:
    st.subheader("Cosa comprano e vendono BlackRock, Vanguard e State Street (13F)")
    if snap.holdings.empty:
        st.info("Dati 13F non disponibili (controlla le impostazioni SEC).")
    else:
        for m in snap.filings_13f:
            st.caption(f"{m['filer']}: portafoglio al {m['period']}, depositato il {m['filed']}")
        pivot = snap.holdings.pivot_table(index="ticker", columns="filer", values="change_pct").round(2)
        st.dataframe(pivot, width="stretch", column_config={c: st.column_config.NumberColumn(c, format="%+.2f%%") for c in pivot.columns})
        st.caption("Variazione del numero di azioni tra gli ultimi due trimestri. I 13F escono fino a 45 giorni "
                   "dopo la fine del trimestre: mostrano movimenti passati, non in tempo reale.")
    st.subheader("Comunicazioni SEC 8-K (ultimi 7 giorni)")
    if snap.filings.empty:
        st.caption("Nessun 8-K recente.")
    else:
        st.dataframe(snap.filings, hide_index=True, width="stretch",
                     column_config={"url": st.column_config.LinkColumn("Documento")})


def tab_alerts(cfg: dict, snap) -> None:
    last = REPORT_DIR / "ultimo_report.txt"
    if st.button("▶️ Esegui il controllo adesso (e invia gli avvisi)", type="primary"):
        with st.spinner("Controllo in corso…"):
            res = run_once(cfg, notify=True, snap=snap)
        st.success(f"{len(res['alerts'])} novità, {len(res['pushed'])} inviate. {' · '.join(res['status'])}")
    if last.exists():
        st.caption(f"Ultimo controllo: {datetime.fromtimestamp(last.stat().st_mtime):%d/%m/%Y %H:%M}")
        st.code(last.read_text(encoding="utf-8"), language=None, wrap_lines=True)
    else:
        st.info("Nessun controllo ancora eseguito.")
    hist = REPORT_DIR / "storico_avvisi.csv"
    if hist.exists():
        st.subheader("Storico avvisi")
        h = pd.read_csv(hist).fillna("").iloc[::-1].head(300)
        st.dataframe(h[["time", "level", "text", "link"]], hide_index=True, width="stretch",
                     column_config={"link": st.column_config.LinkColumn("Link")})


def tab_settings(cfg: dict) -> None:
    with st.form("settings"):
        st.subheader("Avvisi")
        token = st.text_input("Token del bot Telegram", cfg["telegram_token"], type="password",
                              help="Crealo scrivendo /newbot a @BotFather su Telegram.")
        chat = st.text_input("Chat ID Telegram", cfg["telegram_chat_id"])
        desk = st.checkbox("Notifiche sul desktop", cfg["notifiche_desktop"])
        level = st.selectbox("Invia avvisi di livello almeno", ["info", "media", "alta"],
                             index=["info", "media", "alta"].index(cfg["livello_minimo_avviso"]))
        always = st.checkbox("Invia il riepilogo ad ogni controllo anche senza novità", cfg["invia_riepilogo_sempre"])
        st.subheader("Fonti")
        ua = st.text_input("Nome ed email per la SEC (obbligatorio per EDGAR)", cfg["sec_user_agent"],
                           placeholder="Mario Rossi mario.rossi@email.it")
        c13, c8 = st.columns(2)
        f13 = c13.checkbox("Analizza i 13F di BlackRock / Vanguard / State Street", cfg["controlla_13f"])
        f8 = c8.checkbox("Controlla le comunicazioni 8-K", cfg["controlla_8k"])
        wl = st.text_area("Watchlist (ticker separati da virgola)", ", ".join(cfg["watchlist"]), height=120,
                          help="Titoli USA come su Yahoo Finance (es. BRK-B). Nomi e settori sono già noti per: "
                               + ", ".join(UNIVERSE))
        if st.form_submit_button("💾 Salva", type="primary"):
            cfg.update(telegram_token=token.strip(), telegram_chat_id=chat.strip(), notifiche_desktop=desk,
                       livello_minimo_avviso=level, invia_riepilogo_sempre=always, sec_user_agent=ua.strip(),
                       controlla_13f=f13, controlla_8k=f8,
                       watchlist=[t.strip().upper() for t in wl.replace("\n", ",").split(",") if t.strip()])
            save_config(cfg)
            st.cache_data.clear()
            st.success("Impostazioni salvate.")

    c1, c2 = st.columns(2)
    if c1.button("🔔 Invia notifica di prova"):
        st.write(notify.notify_all(cfg, "📊 Radar Azioni – prova", "Le notifiche funzionano ✅", "Le notifiche funzionano ✅")
                 or "Nessun canale configurato.")
    if c2.button("🔎 Trova il mio Chat ID Telegram", disabled=not cfg["telegram_token"],
                 help="Prima scrivi un messaggio qualsiasi al tuo bot su Telegram."):
        try:
            ids = notify.find_chat_ids(cfg["telegram_token"])
            st.write(ids or "Nessun messaggio trovato: scrivi qualcosa al bot e riprova.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Errore: {e}")

    st.subheader("Controllo automatico ogni ora")
    if os.name == "nt":
        installed = scheduler.is_installed()
        st.write("Stato: " + ("✅ attivo" if installed else "❌ non attivo"))
        a, b = st.columns(2)
        if a.button("Attiva controllo orario", disabled=installed):
            ok, msg = scheduler.install()
            (st.success if ok else st.error)(msg)
        if b.button("Disattiva", disabled=not installed):
            ok, msg = scheduler.uninstall()
            (st.success if ok else st.error)(msg)
        st.caption("Usa l'Utilità di pianificazione di Windows: il controllo parte ogni ora anche ad app chiusa "
                   "(il PC deve essere acceso).")
    else:
        st.caption(f"Su questo sistema avvia `python monitor.py --loop 60` oppure pianifica: `{scheduler.task_command()}`")


def main() -> None:
    st.set_page_config(page_title="Stock Radar", page_icon="📈", layout="wide")
    cfg = load_config()
    st.title("📈 " + TITLE)
    if st.sidebar.button("🔄 Aggiorna dati", width="stretch"):
        st.cache_data.clear()
    st.sidebar.caption("Dati aggiornati ogni 15 minuti. Fonti: Fed, BCE, SEC EDGAR, Google News "
                       "(Reuters, Bloomberg, WSJ…), Yahoo Finance. **Non è consulenza finanziaria.**")

    with st.spinner("Raccolgo prezzi, giudizi delle banche, notizie e dati SEC… (fino a un minuto)"):
        snap = load_snapshot(_cfg_key(cfg))
    ranking = score_stocks(snap, cfg["watchlist"])
    if snap.errors:
        with st.expander(f"⚠️ {len(snap.errors)} fonti con problemi"):
            st.write(snap.errors)

    tabs = st.tabs(["🏆 Classifica", "📈 Analisi titolo", "🏛️ Istituzioni & politica", "🏦 Banche d'affari",
                    "🏢 SEC & 13F", "🔔 Avvisi", "⚙️ Impostazioni"])
    if ranking.empty:
        for tab in tabs[:2]:
            tab.error("Prezzi non disponibili: controlla la connessione a Internet.")
    else:
        with tabs[0]:
            header(snap, ranking)
            tab_ranking(ranking)
        with tabs[1]:
            tab_detail(snap, ranking)
    with tabs[2]:
        tab_institutions(snap)
    with tabs[3]:
        tab_analysts(snap)
    with tabs[4]:
        tab_sec(snap)
    with tabs[5]:
        tab_alerts(cfg, snap)
    with tabs[6]:
        tab_settings(cfg)


if __name__ == "__main__":
    main()
