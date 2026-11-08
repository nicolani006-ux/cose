"""Scaricamento dati: feed ufficiali, Google News, prezzi e giudizi analisti, SEC EDGAR."""

from __future__ import annotations

import calendar
import hashlib
import io
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarUltimate/1.0"
SEC_FALLBACK_UA = "RadarUltimate (configura-email-in-impostazioni@example.com)"


RETRY_STATUS = {429, 500, 502, 503, 504}


def http_get(url: str, user_agent: str = BROWSER_UA, timeout: int = 20, session=None, retries: int = 3,
             pause: float = 1.5) -> requests.Response:
    """GET con nuovi tentativi (attesa crescente) su errori di rete, limite di richieste (429) ed errori 5xx."""
    s = session or requests
    for attempt in range(retries):
        try:
            resp = s.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries - 1:
                raise
        else:
            if resp.status_code not in RETRY_STATUS or attempt == retries - 1:
                resp.raise_for_status()
                return resp
            wait = resp.headers.get("Retry-After", "")
            if wait.isdigit():
                time.sleep(min(int(wait), 30))
                continue
        time.sleep(pause * 2 ** attempt)
    raise RuntimeError("non raggiungibile")  # non si arriva qui


# --------------------------------------------------------------------------- #
# Feed RSS (comunicati ufficiali e Google News)
# --------------------------------------------------------------------------- #

def google_news_url(query: str, window: str = "2d") -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} when:{window}")
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def parse_feed(content: bytes, group: str, kind: str, limit: int = 30) -> list[dict]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries[:limit]:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        publisher = ""
        if e.get("source") and isinstance(e.source, dict):
            publisher = e.source.get("title", "")
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        published = None
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        if parsed:
            published = pd.Timestamp(calendar.timegm(parsed), unit="s", tz="UTC")
        link = e.get("link", "")
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
        items.append({
            "id": hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:16],
            "group": group,
            "kind": kind,
            "publisher": publisher or group,
            "title": title,
            "summary": re.sub(r"\s+", " ", summary).strip()[:500],
            "link": link,
            "published": published,
        })
    return items


def fetch_feed(url: str, group: str, kind: str, session=None) -> list[dict]:
    return parse_feed(http_get(url, session=session).content, group, kind)


# --------------------------------------------------------------------------- #
# Prezzi e giudizi degli analisti (Yahoo Finance tramite yfinance)
# --------------------------------------------------------------------------- #

YAHOO_CHART_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")


def yahoo_chart(ticker: str, period: str = "2y", session=None) -> pd.DataFrame | None:
    """Candele giornaliere (rettificate per dividendi e frazionamenti) dall'API dei grafici di Yahoo.
    Riserva per quando la libreria yfinance non funziona (succede quando Yahoo cambia le pagine)."""
    for host in YAHOO_CHART_HOSTS:
        try:
            data = http_get(f"https://{host}/v8/finance/chart/{ticker}?range={period}&interval=1d&events=div,splits",
                            session=session, retries=2).json()
            res = data["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
        except Exception:  # noqa: BLE001 - si prova l'altro server
            continue
        df = pd.DataFrame({"time": pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("America/New_York"),
                           "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                           "volume": q["volume"], "adj": adj}).dropna()
        if df.empty:
            return None
        k = df["adj"] / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * k
        df["time"] = df["time"].dt.normalize()
        return df.drop(columns="adj").reset_index(drop=True)
    return None


def fetch_prices(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    import yfinance as yf

    def one(t):
        try:
            h = yf.Ticker(t).history(period=period, interval="1d", auto_adjust=True)
        except Exception:  # noqa: BLE001 - libreria rotta o Yahoo che rifiuta: si usa l'API dei grafici
            h = None
        if h is None or h.empty:
            return t, yahoo_chart(t, period)
        df = h.reset_index().rename(columns={
            "Date": "time", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume",
        })
        return t, df[["time", "open", "high", "low", "close", "volume"]].dropna()

    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for t, df in pool.map(_safe(one), tickers):
            if df is not None:
                out[t] = df
    return out


def fetch_ratings(tickers: list[str], firms: dict[str, tuple[str, float]], days: int = 45) -> pd.DataFrame:
    """Upgrade/downgrade e target price delle banche indicate negli ultimi `days` giorni."""
    import yfinance as yf

    patterns = [(name, re.compile(rx, re.I), w) for name, (rx, w) in firms.items()]
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)

    def one(t):
        df = yf.Ticker(t).upgrades_downgrades
        return t, df

    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for t, df in pool.map(_safe(one), tickers):
            if df is None or len(df) == 0:
                continue
            df = df.reset_index()
            for _, r in df.iterrows():
                date = pd.Timestamp(r["GradeDate"])
                date = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")
                if date < cutoff:
                    continue
                firm = str(r.get("Firm", ""))
                bank = next(((n, w) for n, rx, w in patterns if rx.search(firm)), None)
                if not bank:
                    continue
                rows.append({
                    "ticker": t, "date": date, "firm": firm, "bank": bank[0], "weight": bank[1],
                    "action": str(r.get("Action", "")).lower(),
                    "from_grade": str(r.get("FromGrade", "") or ""),
                    "to_grade": str(r.get("ToGrade", "") or ""),
                    "pt_now": _num(r.get("currentPriceTarget")),
                    "pt_prev": _num(r.get("priorPriceTarget")),
                })
    cols = ["ticker", "date", "firm", "bank", "weight", "action", "from_grade", "to_grade", "pt_now", "pt_prev"]
    return pd.DataFrame(rows, columns=cols).sort_values("date", ascending=False, ignore_index=True)


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _safe(fn):
    def wrapper(t):
        try:
            return fn(t)
        except Exception:  # noqa: BLE001 - un titolo non deve bloccare gli altri
            return t, None
    return wrapper


# --------------------------------------------------------------------------- #
# SEC EDGAR
# --------------------------------------------------------------------------- #

class Sec:
    """Client minimo per EDGAR con limite di frequenza (max 10 richieste/s)."""

    def __init__(self, user_agent: str, cache_dir: Path, session=None):
        self.ua = user_agent.strip() or SEC_FALLBACK_UA
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._last = 0.0

    def get(self, url: str) -> requests.Response:
        with self._lock:
            wait = 0.12 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        return http_get(url, user_agent=self.ua, timeout=60, session=self.session)

    def ticker_map(self) -> dict[str, int]:
        path = self.cache_dir / "sec_company_tickers.json"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            data = json.loads(path.read_text())
        else:
            data = self.get("https://www.sec.gov/files/company_tickers.json").json()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}

    def submissions(self, cik: int) -> dict:
        return self.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()

    @staticmethod
    def recent_filings(sub: dict) -> pd.DataFrame:
        r = sub.get("filings", {}).get("recent", {})
        n = len(r.get("form", []))
        cols = ["form", "filingDate", "acceptanceDateTime", "accessionNumber", "primaryDocument", "items", "reportDate"]
        return pd.DataFrame({c: (r.get(c) or [""] * n) for c in cols})

    def filing_url(self, cik: int, accession: str, doc: str = "") -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"

    # -- 8-K ---------------------------------------------------------------- #
    def recent_8k(self, tickers: list[str], days: int = 7) -> pd.DataFrame:
        cmap = self.ticker_map()
        cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        rows = []

        def one(t):
            cik = cmap.get(t) or cmap.get(t.replace("-", "."))
            if not cik:
                return []
            f = self.recent_filings(self.submissions(cik))
            f = f[f["form"].isin(["8-K", "8-K/A"]) & (f["filingDate"] >= cutoff)]
            return [{
                "ticker": t, "form": r.form, "date": r.filingDate, "accession": r.accessionNumber,
                "items": r["items"], "url": self.filing_url(cik, r.accessionNumber, r.primaryDocument),
            } for _, r in f.iterrows()]

        with ThreadPoolExecutor(max_workers=4) as pool:
            for res in pool.map(lambda t: _try(one, t, []), tickers):
                rows.extend(res)
        return pd.DataFrame(rows, columns=["ticker", "form", "date", "accession", "items", "url"])

    # -- 13F ---------------------------------------------------------------- #
    def latest_13f(self, ciks: list[int], n: int = 2) -> tuple[int, pd.DataFrame]:
        """Ritorna (cik, ultimi `n` 13F-HR) scegliendo tra i CIK quello più aggiornato."""
        best = (0, pd.DataFrame())
        for cik in ciks:
            try:
                f = self.recent_filings(self.submissions(cik))
            except Exception:  # noqa: BLE001
                continue
            f = f[f["form"] == "13F-HR"].sort_values("filingDate", ascending=False).head(n)
            if not f.empty and (best[1].empty or f["filingDate"].iloc[0] > best[1]["filingDate"].iloc[0]):
                best = (cik, f.reset_index(drop=True))
        return best

    def holdings_13f(self, cik: int, accession: str) -> pd.DataFrame:
        cache = self.cache_dir / f"13f_{cik}_{accession}.csv"
        if cache.exists():
            return pd.read_csv(cache)
        index = self.get(self.filing_url(cik, accession, "index.json")).json()
        files = [i for i in index["directory"]["item"] if i["name"].lower().endswith(".xml")]
        table = [i for i in files if i["name"].lower() != "primary_doc.xml"]
        if not table:
            raise ValueError(f"Tabella 13F non trovata in {accession}")
        table.sort(key=lambda i: ("info" not in i["name"].lower() and "table" not in i["name"].lower(),
                                  -int(i.get("size") or 0)))
        df = parse_13f_xml(self.get(self.filing_url(cik, accession, table[0]["name"])).content)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache, index=False)
        return df


def _try(fn, arg, default):
    try:
        return fn(arg)
    except Exception:  # noqa: BLE001
        return default


def parse_13f_xml(content: bytes) -> pd.DataFrame:
    """Legge l'information table di un 13F (anche molto grande) senza caricarla tutta in memoria."""
    rows = []
    cur: dict = {}
    for _, el in ET.iterparse(io.BytesIO(content), events=("end",)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("nameOfIssuer", "titleOfClass", "cusip", "value", "sshPrnamt", "sshPrnamtType", "putCall"):
            cur[tag] = (el.text or "").strip()
        elif tag == "infoTable":
            if not cur.get("putCall"):
                rows.append({
                    "issuer": cur.get("nameOfIssuer", ""),
                    "cls": cur.get("titleOfClass", ""),
                    "cusip": cur.get("cusip", ""),
                    "value": float(cur.get("value") or 0),
                    "shares": float(cur.get("sshPrnamt") or 0) if cur.get("sshPrnamtType", "SH") == "SH" else 0.0,
                })
            cur = {}
            el.clear()
    return pd.DataFrame(rows, columns=["issuer", "cls", "cusip", "value", "shares"])
