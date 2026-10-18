"""Controllo periodico: raccoglie i dati, calcola i punteggi e invia gli avvisi sulle novità.

Uso:  python monitor.py              (un controllo, poi esce: adatto all'Utilità di pianificazione)
      python monitor.py --loop 60    (resta aperto e controlla ogni 60 minuti)
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from stocks import sources
from stocks.analysis import enrich_news, holdings_change, macro_tone, score_stocks
from stocks.config import (
    ANALYST_FIRMS, CACHE_DIR, FILERS_13F, NEWS_QUERIES, OFFICIAL_FEEDS, REPORT_DIR, STATE_FILE,
    load_config, ticker_info,
)
from stocks.notify import notify_all

LEVELS = {"info": 0, "media": 1, "alta": 2}
LEVEL_ICON = {"alta": "🔴", "media": "🟠", "info": "🔵"}

ITEMS_8K = {
    "1.01": "accordo rilevante", "1.02": "fine di un accordo rilevante", "1.03": "bancarotta",
    "2.01": "acquisizione/cessione completata", "2.02": "risultati trimestrali",
    "2.03": "nuovo debito", "2.05": "ristrutturazione", "2.06": "svalutazione",
    "3.01": "rischio delisting", "3.02": "emissione di azioni", "4.01": "cambio revisore",
    "4.02": "bilanci precedenti non affidabili", "5.01": "cambio di controllo",
    "5.02": "cambio dirigenti", "5.07": "voto assembleare", "7.01": "comunicazione al mercato",
    "8.01": "altri eventi",
}
IMPORTANT_8K = {"1.01", "1.03", "2.01", "2.02", "2.05", "2.06", "3.01", "4.01", "4.02", "5.01", "5.02"}
NEGATIVE_8K = {"1.03", "2.06", "3.01", "4.01", "4.02"}
MONETARY = re.compile(r"FOMC|monetary policy|federal funds|interest rate|target range|policy rate|"
                      r"key ECB interest|beige book", re.I)
MAX_AGE = pd.Timedelta(days=3)
NEWS_COLUMNS = ["id", "group", "kind", "publisher", "title", "summary", "link", "published"]


@dataclass
class Snapshot:
    fetched_at: pd.Timestamp
    prices: dict = field(default_factory=dict)
    ratings: pd.DataFrame = field(default_factory=pd.DataFrame)
    news: pd.DataFrame = field(default_factory=pd.DataFrame)
    filings: pd.DataFrame = field(default_factory=pd.DataFrame)
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    filings_13f: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    watchlist: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Raccolta dati
# --------------------------------------------------------------------------- #

def _collect_news(errors: list) -> pd.DataFrame:
    jobs = [(name, url, kind) for name, (url, kind) in OFFICIAL_FEEDS.items()]
    jobs += [(name, sources.google_news_url(q), "notizie") for name, q in NEWS_QUERIES.items()]

    def one(job):
        name, url, kind = job
        try:
            return sources.fetch_feed(url, name, kind)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}")
            return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        items = [i for res in pool.map(one, jobs) for i in res]
    return pd.DataFrame(items, columns=NEWS_COLUMNS).drop_duplicates("id")


def _collect_13f(sec, watchlist: list[str], errors: list) -> tuple[pd.DataFrame, list]:
    frames, meta = [], []
    for filer, ciks in FILERS_13F.items():
        try:
            cik, f = sec.latest_13f(ciks)
            if len(f) < 2:
                errors.append(f"13F {filer}: meno di due moduli disponibili")
                continue
            latest = sec.holdings_13f(cik, f["accessionNumber"].iloc[0])
            prev = sec.holdings_13f(cik, f["accessionNumber"].iloc[1])
            ch = holdings_change(latest, prev, watchlist).assign(filer=filer)
            frames.append(ch)
            meta.append({"filer": filer, "accession": f["accessionNumber"].iloc[0],
                         "filed": f["filingDate"].iloc[0], "period": f["reportDate"].iloc[0]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"13F {filer}: {type(e).__name__}: {e}"[:160])
    cols = ["ticker", "shares_prev", "shares_now", "change_pct", "filer"]
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)), meta


SOURCE_TIMEOUT = 150  # secondi: oltre, la fonte viene saltata e segnalata
SNAPSHOT_FILE = CACHE_DIR / "ultimo_snapshot.pkl"


def collect(cfg: dict, progress=None, timeout: float = SOURCE_TIMEOUT) -> Snapshot:
    """Scarica tutte le fonti in parallelo. `progress(nome_fonte, ok)` viene chiamata
    (dal thread chiamante) man mano che ogni fonte termina."""
    wl = cfg["watchlist"]
    snap = Snapshot(fetched_at=pd.Timestamp.now(tz="UTC"), watchlist=list(wl))
    errors: list[str] = []
    sec = sources.Sec(cfg.get("sec_user_agent", ""), CACHE_DIR)
    empty_holdings = pd.DataFrame(columns=["ticker", "shares_prev", "shares_now", "change_pct", "filer"])

    jobs = {
        "Notizie e comunicati": (lambda: _collect_news(errors), pd.DataFrame(columns=NEWS_COLUMNS)),
        "Prezzi": (lambda: sources.fetch_prices(wl), {}),
        "Giudizi delle banche": (lambda: sources.fetch_ratings(wl, ANALYST_FIRMS), pd.DataFrame()),
    }
    if cfg.get("controlla_8k"):
        jobs["SEC 8-K"] = (lambda: sec.recent_8k(wl), pd.DataFrame())
    if cfg.get("controlla_13f"):
        jobs["SEC 13F"] = (lambda: _collect_13f(sec, wl, errors), (empty_holdings, []))

    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {pool.submit(fn): label for label, (fn, _) in jobs.items()}
    results: dict = {}
    try:
        for fut in as_completed(futures, timeout=timeout):
            label = futures[fut]
            try:
                results[label] = fut.result()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{label}: {type(e).__name__}: {e}"[:160])
            if progress:
                progress(label, label in results)
    except TimeoutError:
        for fut, label in futures.items():
            if not fut.done():
                errors.append(f"{label}: tempo scaduto (oltre {timeout:.0f} s)")
                if progress:
                    progress(label, False)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    def get(label):
        return results.get(label, jobs[label][1]) if label in jobs else None

    snap.news = enrich_news(get("Notizie e comunicati"), wl)
    snap.prices = get("Prezzi")
    snap.ratings = get("Giudizi delle banche")
    snap.filings = get("SEC 8-K") if "SEC 8-K" in jobs else pd.DataFrame()
    if "SEC 13F" in jobs:
        snap.holdings, snap.filings_13f = get("SEC 13F")
    if not snap.prices and "Prezzi" in results:
        errors.append("Prezzi: nessun dato scaricato (Yahoo Finance non raggiungibile?)")
    if not cfg.get("sec_user_agent"):
        errors.append("SEC: imposta nome ed email nelle impostazioni (richiesto da EDGAR)")
    snap.errors = errors
    return snap


def save_snapshot(snap: Snapshot) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_FILE.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(snap))
    tmp.replace(SNAPSHOT_FILE)


def load_snapshot(max_age_min: float = 60, watchlist: list[str] | None = None) -> Snapshot | None:
    """Ultimo snapshot salvato dal controllo orario/bot, se abbastanza recente."""
    try:
        snap = pickle.loads(SNAPSHOT_FILE.read_bytes())
    except Exception:  # noqa: BLE001 - file assente, vecchio o di un'altra versione
        return None
    if pd.Timestamp.now(tz="UTC") - snap.fetched_at > pd.Timedelta(minutes=max_age_min):
        return None
    if watchlist is not None and set(watchlist) != set(getattr(snap, "watchlist", [])):
        return None
    return snap


# --------------------------------------------------------------------------- #
# Avvisi
# --------------------------------------------------------------------------- #

def _alert(key: str, level: str, text: str, link: str = "") -> dict:
    return {"key": key, "level": level, "text": text, "link": link}


def _fresh(ts, now) -> bool:
    return ts is None or pd.isna(ts) or now - ts <= MAX_AGE


def build_alerts(snap: Snapshot, ranking: pd.DataFrame, state: dict) -> list[dict]:
    now = snap.fetched_at
    alerts = []
    for r in snap.news.itertuples():
        if not _fresh(r.published, now):
            continue
        tickers = list(r.tickers)
        if r.kind != "notizie":
            if r.kind == "regolatore":
                level = "info"
            else:
                level = "alta" if MONETARY.search(r.title) else "media"
            tone = ""
            if r.tone is not None and not pd.isna(r.tone):
                tone = " (tono accomodante 🕊️)" if r.tone > 0 else (" (tono restrittivo 🦅)" if r.tone < 0 else "")
            alerts.append(_alert(f"news:{r.id}", level, f"🏛️ {r.group}: {r.title}{tone}", r.link))
        else:
            strong = abs(r.sentiment) >= 0.6
            level = "media" if (tickers and abs(r.sentiment) >= 0.34) or strong else "info"
            tag = f" → {', '.join(tickers)}" if tickers else ""
            mood = " 📈" if r.sentiment >= 0.34 else (" 📉" if r.sentiment <= -0.34 else "")
            alerts.append(_alert(f"news:{r.id}", level, f"📰 {r.group} · {r.publisher}: {r.title}{tag}{mood}", r.link))

    for r in snap.ratings.itertuples() if not snap.ratings.empty else []:
        if now - r.date > MAX_AGE:
            continue
        name = ticker_info(r.ticker)[0]
        main = r.action in ("up", "down", "init")
        level = "alta" if main and r.weight >= 1 else ("media" if main else "info")
        verb = {"up": "alza il giudizio", "down": "abbassa il giudizio", "init": "inizia a coprire",
                "main": "conferma", "reit": "ribadisce"}.get(r.action, r.action)
        pt = f", target {r.pt_now:.0f}$" if r.pt_now and not pd.isna(r.pt_now) else ""
        icon = "⬆️" if r.action == "up" else ("⬇️" if r.action == "down" else "🏦")
        alerts.append(_alert(
            f"rating:{r.ticker}:{r.bank}:{r.date:%Y-%m-%d}:{r.action}:{r.to_grade}", level,
            f"{icon} {r.bank} {verb} su {r.ticker} ({name}): {r.from_grade + ' → ' if r.from_grade else ''}{r.to_grade}{pt}",
        ))

    for r in snap.filings.itertuples() if not snap.filings.empty else []:
        items = {i.strip() for i in str(r.items).split(",") if i.strip()}
        if not items & IMPORTANT_8K:
            continue
        desc = ", ".join(ITEMS_8K.get(i, i) for i in sorted(items) if i != "9.01")
        level = "alta" if items & NEGATIVE_8K else "media"
        alerts.append(_alert(f"8k:{r.accession}", level, f"📄 {r.ticker} ({ticker_info(r.ticker)[0]}) – SEC {r.form}: {desc}", r.url))

    for m in snap.filings_13f:
        h = snap.holdings[snap.holdings["filer"] == m["filer"]] if not snap.holdings.empty else pd.DataFrame()
        txt = f"🏢 Nuovo 13F di {m['filer']} (portafoglio al {m['period']})"
        if not h.empty:
            up = h[h["change_pct"] > 0].sort_values("change_pct", ascending=False).head(3)
            down = h[h["change_pct"] < 0].sort_values("change_pct").head(3)
            parts = []
            if not up.empty:
                parts.append("aumenta " + ", ".join(f"{r.ticker} {r.change_pct:+.1f}%" for r in up.itertuples()))
            if not down.empty:
                parts.append("riduce " + ", ".join(f"{r.ticker} {r.change_pct:+.1f}%" for r in down.itertuples()))
            if parts:
                txt += ": " + "; ".join(parts)
        alerts.append(_alert(f"13f:{m['filer']}:{m['accession']}", "media", txt))

    prev = state.get("last_signal", {})
    for r in ranking.itertuples() if not ranking.empty else []:
        before = prev.get(r.ticker)
        if before is None or before == r.signal:
            continue
        if "forte" in r.signal or ("Acquisto" in r.signal and "Acquisto" not in before):
            level = "alta" if "forte" in r.signal else "media"
            alerts.append(_alert(
                f"signal:{r.ticker}:{r.signal}:{now:%Y-%m-%d %H}", level,
                f"🎯 {r.ticker} ({r.name}) passa da {before} a {r.signal} (punteggio {r.score:+.0f})",
            ))
    alerts.sort(key=lambda a: -LEVELS[a["level"]])
    return alerts


def format_message(alerts: list[dict], ranking: pd.DataFrame, snap: Snapshot, first_run: bool,
                   max_alerts: int = 15) -> str:
    lines = []
    if first_run:
        lines.append("✅ Monitoraggio attivato. Da ora riceverai solo le novità.")
    if alerts:
        lines.append(f"🔔 Novità ({len(alerts)}):")
        for a in alerts[:max_alerts]:
            lines.append(f"{LEVEL_ICON[a['level']]} {a['text']}" + (f"\n   {a['link']}" if a["link"] else ""))
        if len(alerts) > max_alerts:
            lines.append(f"…e altre {len(alerts) - max_alerts} (vedi l'app)")
    if not ranking.empty:
        buy = ranking[ranking["score"] >= 20].head(5)
        lines.append("")
        if buy.empty:
            lines.append("🏆 Nessun titolo ha ora un segnale di acquisto chiaro.")
        else:
            lines.append("🏆 Migliori candidati all'acquisto ora:")
            for i, r in enumerate(buy.itertuples(), 1):
                lines.append(f"{i}. {r.ticker} ({r.name}) {r.score:+.0f} {r.signal} — {r.reasons[:220]}")
        avoid = ranking[ranking["score"] <= -20].tail(3)
        if not avoid.empty:
            lines.append("📉 Da evitare: " + ", ".join(f"{r.ticker} {r.score:+.0f}" for r in avoid.itertuples()))
    tone = macro_tone(snap.news)
    lines.append(f"🏛️ Fed: tono {'accomodante' if tone > 0.15 else 'restrittivo' if tone < -0.15 else 'neutro'} ({tone:+.2f})")
    if snap.errors:
        lines.append(f"⚠️ Fonti con problemi: {'; '.join(snap.errors[:5])}")
    lines.append("ℹ️ Indicazioni automatiche, non sono consulenza finanziaria.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stato e report
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def save_report(result: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "ultimo_report.txt").write_text(result["message"], encoding="utf-8")
    ranking = result["ranking"]
    data = {
        "time": result["time"], "status": result["status"], "errors": result["errors"],
        "alerts": result["alerts"],
        "ranking": ranking.drop(columns=["spark"], errors="ignore").to_dict("records") if not ranking.empty else [],
    }
    (REPORT_DIR / "ultimo_report.json").write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    if result["alerts"]:
        hist = REPORT_DIR / "storico_avvisi.csv"
        pd.DataFrame(result["alerts"]).assign(time=result["time"]).to_csv(
            hist, mode="a", header=not hist.exists(), index=False, encoding="utf-8")


def run_once(cfg: dict | None = None, notify: bool = True, snap: Snapshot | None = None) -> dict:
    cfg = cfg or load_config()
    state = load_state()
    # Primo giro = nessun avviso ancora registrato (anche se i giri precedenti sono falliti
    # per mancanza di rete): si registra tutto come "già visto" senza inondare di messaggi.
    first_run = not state.get("seen")
    if snap is None:
        snap = collect(cfg)
        if snap.prices:
            save_snapshot(snap)
    ranking = score_stocks(snap, cfg["watchlist"])
    alerts = build_alerts(snap, ranking, state)

    seen = state.get("seen", {})
    new = [a for a in alerts if a["key"] not in seen]
    min_level = LEVELS.get(cfg.get("livello_minimo_avviso", "media"), 1)
    to_push = [] if first_run else [a for a in new if LEVELS[a["level"]] >= min_level]

    now_iso = snap.fetched_at.isoformat()
    seen.update({a["key"]: now_iso for a in alerts})
    cutoff = (snap.fetched_at - pd.Timedelta(days=30)).isoformat()
    state = {
        "seen": {k: v for k, v in seen.items() if v >= cutoff},
        "last_signal": dict(zip(ranking["ticker"], ranking["signal"])) if not ranking.empty else state.get("last_signal", {}),
        "last_run": now_iso,
    }
    save_state(state)

    message = format_message(to_push, ranking, snap, first_run)
    status = []
    if notify and (to_push or (first_run and not ranking.empty) or cfg.get("invia_riepilogo_sempre")):
        title = f"📊 Radar Azioni – {datetime.now():%d/%m %H:%M}"
        short = to_push[0]["text"] if to_push else "Controllo completato: apri l'app per la classifica."
        if len(to_push) > 1:
            short += f" (+{len(to_push) - 1} altre novità)"
        status = notify_all(cfg, title, message, short)
    result = {"time": datetime.now().isoformat(timespec="seconds"), "message": message, "alerts": new,
              "pushed": to_push, "ranking": ranking, "status": status, "errors": snap.errors,
              "first_run": first_run, "snapshot": snap}
    save_report(result)
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Radar Azioni: controllo e avvisi")
    p.add_argument("--loop", type=int, metavar="MINUTI", help="ripeti il controllo ogni N minuti")
    p.add_argument("--no-notify", action="store_true", help="non inviare notifiche, stampa soltanto")
    p.add_argument("--test-notifica", action="store_true", help="invia una notifica di prova ed esci")
    args = p.parse_args(argv)

    if args.test_notifica:
        print("\n".join(notify_all(load_config(), "📊 Radar Azioni – prova", "Le notifiche funzionano ✅", "Le notifiche funzionano ✅")) or
              "Nessun canale configurato (Telegram o notifiche desktop).")
        return 0
    while True:
        res = run_once(notify=not args.no_notify)
        print(f"[{res['time']}] {len(res['alerts'])} novità, {len(res['pushed'])} inviate {res['status']}")
        print(res["message"], flush=True)
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    raise SystemExit(main())
