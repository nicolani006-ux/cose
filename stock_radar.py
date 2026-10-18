"""Stock Radar: classifica delle azioni, banche d'affari, Fed/BCE, SEC (8-K e 13F) e avvisi."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime

import pandas as pd
import streamlit as st

import nav
from charts import macd_chart, price_chart, rsi_chart
from indicators import add_indicators
from stocks.analysis import macro_tone, score_stocks
from stocks.config import REPORT_DIR, load_config, ticker_info
from stocks.monitor import collect, load_snapshot, run_once, save_snapshot

TITLE = "Stock Radar – Azioni & Istituzioni"


FRESH_SECONDS = 900  # i dati in memoria vengono riusati per 15 minuti


@st.cache_resource
def _memory() -> dict:
    return {}


def get_snapshot(cfg: dict, force: bool = False):
    """Dati dalla memoria, dall'ultimo controllo del bot (se recente) o scaricati ora mostrando i progressi."""
    mem, key = _memory(), _cfg_key(cfg)
    if not force:
        hit = mem.get(key)
        if hit and time.time() - hit[0] < FRESH_SECONDS:
            return hit[1]
        disk = load_snapshot(max_age_min=60, watchlist=cfg["watchlist"])
        if disk is not None:
            mem[key] = (disk.fetched_at.timestamp(), disk)
            return disk
    with st.status("Raccolgo prezzi, giudizi delle banche, notizie e dati SEC… (1–2 minuti la prima volta)",
                   expanded=True) as status:
        st.caption("Intanto puoi aprire ⚙️ Impostazioni & Bot dal menu a sinistra.")
        snap = collect(cfg, progress=lambda label, ok: status.write(("✅ " if ok else "⚠️ ") + label))
        status.update(label="Dati pronti", state="complete", expanded=False)
    if snap.prices:
        save_snapshot(snap)
    mem[key] = (time.time(), snap)
    return snap


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


def main() -> None:
    st.set_page_config(page_title="Stock Radar", page_icon="📈", layout="wide")
    cfg = load_config()
    st.title("📈 " + TITLE)
    force = st.sidebar.button("🔄 Aggiorna dati", width="stretch")
    st.sidebar.caption("Fonti: Fed, BCE, SEC EDGAR, Google News (Reuters, Bloomberg, WSJ…), Yahoo Finance. "
                       "**Non è consulenza finanziaria.**")
    if not cfg["telegram_token"] or not cfg["sec_user_agent"]:
        missing = [x for x, ok in (("il bot Telegram", cfg["telegram_token"]),
                                   ("nome ed email per la SEC", cfg["sec_user_agent"])) if not ok]
        st.info("Da configurare: " + " e ".join(missing) + ".")
        st.page_link(nav.settings_page(), label="Apri Impostazioni & Bot", icon="⚙️")

    snap = get_snapshot(cfg, force=force)
    ranking = score_stocks(snap, cfg["watchlist"])
    st.caption(f"Dati del {snap.fetched_at.tz_convert(None):%d/%m/%Y %H:%M} UTC")
    if snap.errors:
        with st.expander(f"⚠️ {len(snap.errors)} fonti con problemi"):
            st.write(snap.errors)

    tabs = st.tabs(["🏆 Classifica", "📈 Analisi titolo", "🏛️ Istituzioni & politica", "🏦 Banche d'affari",
                    "🏢 SEC & 13F", "🔔 Avvisi"])
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


if __name__ == "__main__":
    main()
