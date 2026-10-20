"""Sentiment delle notizie, tono delle banche centrali e punteggio complessivo per titolo."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from indicators import analyze, plan_and_stats
from stocks.config import ticker_info

# --------------------------------------------------------------------------- #
# Sentiment (dizionario finanziario semplificato, titoli in inglese)
# --------------------------------------------------------------------------- #

POSITIVE = [
    "beat", "beats", "tops", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies",
    "record high", "upgrade", "upgrades", "upgraded", "raises", "raised", "boost", "boosts", "strong",
    "stronger", "gain", "gains", "bullish", "outperform", "overweight", "buy rating", "approval",
    "approves", "approved", "wins", "rebound", "rebounds", "exceeds", "accelerates", "upbeat",
    "optimistic", "breakthrough", "expands", "buyback", "dividend hike", "raises guidance", "lifts",
    "climbs", "higher", "top pick",
]
NEGATIVE = [
    "miss", "misses", "missed", "plunge", "plunges", "slump", "slumps", "falls", "drop", "drops",
    "tumble", "tumbles", "sink", "sinks", "slides", "downgrade", "downgrades", "downgraded", "weak",
    "weaker", "loss", "losses", "lawsuit", "sues", "probe", "investigation", "recall", "bearish",
    "underweight", "underperform", "layoffs", "job cuts", "bankruptcy", "default", "fraud", "warns",
    "warning", "slowdown", "recession", "tariff", "tariffs", "sanctions", "decline", "declines",
    "crash", "selloff", "sell-off", "fears", "cuts guidance", "lowers guidance", "cuts forecast",
    "pessimistic", "halts", "shortage", "antitrust", "lower", "slashes",
]
DOVISH = [
    "rate cut", "rate cuts", "cut rates", "cuts rates", "cut interest rates", "lower rates",
    "lower interest rates", "lowers rates", "easing", "dovish", "accommodative", "stimulus",
    "inflation cooling", "inflation eases", "inflation easing", "weakening labor", "softening labor",
    "downside risks to employment", "reduce the target range", "lowered the target range",
]
HAWKISH = [
    "rate hike", "rate hikes", "raise rates", "raises rates", "raised rates", "hike rates", "tightening",
    "hawkish", "restrictive", "higher for longer", "inflation remains elevated", "elevated inflation",
    "persistent inflation", "upside risks to inflation", "raise the target range",
    "raised the target range", "balance sheet reduction",
]


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b", re.I)


_POS, _NEG, _DOV, _HAWK = _rx(POSITIVE), _rx(NEGATIVE), _rx(DOVISH), _rx(HAWKISH)


def sentiment(text: str) -> float:
    """-1 (molto negativo) … +1 (molto positivo)."""
    # "rate cut" non è una notizia negativa: viene valutata dal tono delle banche centrali
    text = _DOV.sub(" ", _HAWK.sub(" ", text or ""))
    p, n = len(_POS.findall(text)), len(_NEG.findall(text))
    return 0.0 if p + n == 0 else (p - n) / (p + n)


def cb_tone(text: str) -> float | None:
    """+1 accomodante (tassi giù), -1 restrittivo (tassi su), None se non pertinente."""
    d, h = len(_DOV.findall(text or "")), len(_HAWK.findall(text or ""))
    return None if d + h == 0 else (d - h) / (d + h)


# Effetto (semplificato) di una politica monetaria accomodante sui settori.
SECTOR_RATE_SENSITIVITY = {
    "Tecnologia": 1.0, "Immobiliare": 1.0, "Consumi discrezionali": 0.8, "Utility": 0.6,
    "Comunicazioni": 0.6, "Industria": 0.4, "Salute": 0.2, "Energia": 0.2, "Consumi di base": 0.0,
    "Finanza": -0.3, "Altro": 0.3,
}

# --------------------------------------------------------------------------- #
# Collegamento notizie -> titoli
# --------------------------------------------------------------------------- #

_ANALYST_VERB = (r"\s+(?:analysts?\s+|strategists?\s+|economists?\s+)?(?:upgrades?|downgrades?|raises?|cuts?|"
                 r"lifts?|lowers?|says|sees|expects|predicts|forecasts|recommends|initiates|starts|"
                 r"strategists?|economists?|analysts?|survey)\b")


def _name_patterns(watchlist: list[str]) -> dict[str, tuple[re.Pattern, re.Pattern | None]]:
    out = {}
    for t in watchlist:
        names = ticker_info(t)[2]
        alts = [re.escape(n) for n in names] + [rf"\(\s*(?:NYSE|NASDAQ|Nasdaq)?:?\s*{re.escape(t)}\s*\)",
                                                 rf"\${re.escape(t)}\b"]
        mention = re.compile(r"(?<![\w&])(?:" + "|".join(alts) + r")(?![\w&])")
        as_analyst = re.compile(r"(?:" + "|".join(re.escape(n) for n in names) + ")" + _ANALYST_VERB, re.I) if names else None
        out[t] = (mention, as_analyst)
    return out


def tag_tickers(text: str, patterns: dict) -> list[str]:
    """Titoli citati in un testo. Una banca che *commenta* altri titoli
    ("Goldman Sachs upgrades Nvidia") non conta come notizia sulla banca stessa."""
    found = []
    for t, (mention, as_analyst) in patterns.items():
        hits = mention.findall(text)
        if not hits:
            continue
        if as_analyst and len(as_analyst.findall(text)) >= len(hits):
            continue
        found.append(t)
    return found


def enrich_news(news: pd.DataFrame, watchlist: list[str]) -> pd.DataFrame:
    if news.empty:
        return news.assign(sentiment=[], tone=[], tickers=[])
    pats = _name_patterns(watchlist)
    text = news["title"].fillna("") + ". " + news["summary"].fillna("")
    news = news.copy()
    news["sentiment"] = news["title"].map(sentiment)
    news["tone"] = [cb_tone(x) if k.startswith("banca_centrale") else None for x, k in zip(text, news["kind"])]
    news["tickers"] = news["title"].map(lambda x: tag_tickers(x, pats))
    return news


# --------------------------------------------------------------------------- #
# 13F
# --------------------------------------------------------------------------- #

_SUFFIXES = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "NEW", "DEL", "THE", "AND", "COM"}


def normalize_issuer(name: str) -> str:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper()).split()
    return " ".join(t for t in tokens if t not in _SUFFIXES)


def watchlist_shares(holdings: pd.DataFrame, watchlist: list[str]) -> dict[str, float]:
    if holdings.empty:
        return {}
    keys = {}
    for t in watchlist:
        for n in ticker_info(t)[3]:
            keys[normalize_issuer(n)] = t
    norm = holdings["issuer"].map(normalize_issuer)
    sel = holdings.assign(ticker=norm.map(keys)).dropna(subset=["ticker"])
    return sel.groupby("ticker")["shares"].sum().to_dict()


def holdings_change(latest: pd.DataFrame, previous: pd.DataFrame, watchlist: list[str]) -> pd.DataFrame:
    now, prev = watchlist_shares(latest, watchlist), watchlist_shares(previous, watchlist)
    rows = []
    for t in sorted(set(now) | set(prev)):
        a, b = prev.get(t, 0.0), now.get(t, 0.0)
        pct = (b / a - 1) * 100 if a > 0 else (100.0 if b > 0 else 0.0)
        rows.append({"ticker": t, "shares_prev": a, "shares_now": b, "change_pct": pct})
    return pd.DataFrame(rows, columns=["ticker", "shares_prev", "shares_now", "change_pct"])


# --------------------------------------------------------------------------- #
# Giudizi degli analisti
# --------------------------------------------------------------------------- #

BULLISH = re.compile(r"buy|overweight|outperform|positive|accumulate|add\b", re.I)
BEARISH = re.compile(r"sell|underweight|underperform|negative|reduce", re.I)


def rating_points(r, now: pd.Timestamp) -> tuple[float, str]:
    """Punti (-25..+25 circa) e descrizione per un'azione di un analista."""
    age = max((now - r["date"]).days, 0)
    decay = max(0.0, 1 - age / 30)
    to = r["to_grade"]
    pts, what = 0.0, ""
    if r["action"] == "up":
        pts, what = 25, f"upgrade a {to}"
    elif r["action"] == "down":
        pts, what = -25, f"downgrade a {to}"
    elif r["action"] == "init":
        pts = 15 if BULLISH.search(to) else (-15 if BEARISH.search(to) else 0)
        what = f"inizia copertura: {to}"
    else:
        pts = 5 if BULLISH.search(to) else (-5 if BEARISH.search(to) else 0)
        what = f"conferma {to}" if to else "conferma"
    if r.get("pt_now") and r.get("pt_prev"):
        if r["pt_now"] > r["pt_prev"]:
            pts += 5
            what += f", target {r['pt_prev']:.0f}→{r['pt_now']:.0f}$"
        elif r["pt_now"] < r["pt_prev"]:
            pts -= 5
            what += f", target {r['pt_prev']:.0f}→{r['pt_now']:.0f}$"
    return pts * decay * r["weight"], f"{r['bank']}: {what}"


# --------------------------------------------------------------------------- #
# Punteggio complessivo
# --------------------------------------------------------------------------- #

STOCK_HORIZON_DAYS = 20  # durata massima di un'operazione nella statistica storica (giorni di borsa)
WEIGHTS = {"tecnico": 0.35, "analisti": 0.30, "notizie": 0.15, "istituzioni": 0.10, "macro": 0.10}
NEGATIVE_8K_ITEMS = {"1.03", "2.06", "3.01", "4.01", "4.02"}


def signal_label(score: float) -> str:
    if score >= 50:
        return "🟢 Acquisto forte"
    if score >= 20:
        return "🟢 Acquisto"
    if score <= -50:
        return "🔴 Vendita forte"
    if score <= -20:
        return "🔴 Evitare"
    return "⚪ Neutrale"


def macro_tone(news: pd.DataFrame, days: int = 10) -> float:
    """Tono medio della Fed negli ultimi giorni (+1 accomodante, -1 restrittivo)."""
    if news.empty or "tone" not in news:
        return 0.0
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    fed = news[(news["kind"] == "banca_centrale") & news["tone"].notna()]
    fed = fed[fed["published"].isna() | (fed["published"] >= cutoff)]
    return float(fed["tone"].mean()) if not fed.empty else 0.0


def score_stocks(snap, watchlist: list[str]) -> pd.DataFrame:
    """Combina analisi tecnica, analisti, notizie, 13F e contesto macro (-100..+100 per titolo)."""
    now = pd.Timestamp.now(tz="UTC")
    tone = macro_tone(snap.news)
    news_recent = snap.news
    if not news_recent.empty:
        news_recent = news_recent[news_recent["published"].isna()
                                  | (news_recent["published"] >= now - pd.Timedelta(hours=72))]
    rows = []
    for t in watchlist:
        df = snap.prices.get(t)
        if df is None or len(df) < 60:
            continue
        name, sector, _, _ = ticker_info(t)
        reasons: list[str] = []
        tech = analyze(df)
        reasons.append(f"Tecnico {tech['score']:+.0f} (trend {tech['trend_ema'].lower()}, RSI {tech['rsi']:.0f})")
        if tech["alerts"]:
            reasons.append(tech["alerts"])

        a_score = 0.0
        if not snap.ratings.empty:
            for _, r in snap.ratings[snap.ratings["ticker"] == t].iterrows():
                pts, desc = rating_points(r, now)
                a_score += pts
                if abs(pts) >= 2:
                    reasons.append(desc)
        a_score = float(np.clip(a_score * 2, -100, 100))

        n_score, n_count = 0.0, 0
        if not news_recent.empty:
            mine = news_recent[news_recent["tickers"].map(lambda x: t in x)]
            n_count = len(mine)
            if n_count:
                n_score = float(np.clip(mine["sentiment"].mean() * 100 * min(1, n_count / 3), -100, 100))
                reasons.append(f"{n_count} {'notizia' if n_count == 1 else 'notizie'}, sentiment {mine['sentiment'].mean():+.2f}")

        i_score = 0.0
        if not snap.holdings.empty:
            h = snap.holdings[snap.holdings["ticker"] == t]
            if not h.empty:
                pct = h["change_pct"].mean()
                i_score = float(np.clip(pct * 10, -100, 100))
                if abs(pct) >= 1:
                    who = ", ".join(f"{r.filer} {r.change_pct:+.1f}%" for r in h.itertuples())
                    reasons.append(f"13F: {who}")

        if not snap.filings.empty:
            for _, f in snap.filings[snap.filings["ticker"] == t].iterrows():
                if set(str(f["items"]).split(",")) & NEGATIVE_8K_ITEMS:
                    n_score = max(-100.0, n_score - 40)
                    reasons.append(f"8-K negativo ({f['items']})")

        m_score = tone * SECTOR_RATE_SENSITIVITY.get(sector, 0.3) * 100
        if abs(tone) >= 0.2 and abs(m_score) >= 10:
            reasons.append(f"Fed {'accomodante' if tone > 0 else 'restrittiva'}: {'favorevole' if m_score > 0 else 'sfavorevole'} a {sector}")

        parts = {"tecnico": tech["score"], "analisti": a_score, "notizie": n_score,
                 "istituzioni": i_score, "macro": m_score}
        score = float(np.clip(sum(WEIGHTS[k] * v for k, v in parts.items()), -100, 100))
        close = df["close"]
        rows.append({
            "ticker": t, "name": name, "sector": sector, "price": float(close.iloc[-1]),
            "chg_1d": (close.iloc[-1] / close.iloc[-2] - 1) * 100,
            "chg_1m": (close.iloc[-1] / close.iloc[-22] - 1) * 100 if len(close) > 22 else np.nan,
            **{f"s_{k}": round(v, 1) for k, v in parts.items()},
            "score": round(score, 1), "signal": signal_label(score),
            **plan_and_stats(df, horizon=STOCK_HORIZON_DAYS),
            "reasons": " · ".join(reasons), "spark": close.iloc[-60:].round(4).tolist(),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("score", ascending=False, ignore_index=True)
    return out
