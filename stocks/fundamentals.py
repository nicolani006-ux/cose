"""Bilanci dalla SEC (XBRL company facts): ricavi, utili, EPS e margini, trimestre per trimestre.

Calcoli della skill trader-analyst (day-trader-skill di J. Schincariol, licenza MIT); qui si
scaricano i dati con il client SEC dell'app (User-Agent con nome ed email dalle impostazioni)
e si tengono in cache per 12 ore.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from stocks.skills import load_skill

CACHE_HOURS = 12


def company_metrics(ticker: str, sec, cache_dir: Path, companyfacts: dict | None = None) -> dict | None:
    """Ultimi numeri di bilancio del titolo, o None se la SEC non li ha (es. società estere IFRS, ETF)."""
    cache = Path(cache_dir) / f"bilanci_{ticker.upper()}.json"
    if companyfacts is None and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_HOURS * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    if companyfacts is None:
        cmap = sec.ticker_map()
        cik = cmap.get(ticker.upper()) or cmap.get(ticker.upper().replace("-", "."))
        if not cik:
            return None
        companyfacts = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json").json()
    facts = load_skill("trader-analyst", "facts")
    res = facts.analyse(companyfacts, quarters=6)
    if not res["rows"]:
        return None
    m = facts.metrics_row(ticker.upper(), res)
    m["quarters"] = [{k: q.get(k) for k in ("fiscal", "end", "revenue", "revenue_yoy", "eps_diluted", "eps_yoy",
                                             "net_margin", "form", "derived")} for q in res["rows"][:4]]
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(m), encoding="utf-8")
    return m


def _money(v) -> str:
    if v is None:
        return "n/d"
    for div, unit in ((1e12, "mila Mld"), (1e9, "Mld"), (1e6, "Mln")):
        if abs(v) >= div:
            return f"{v / div:,.1f} {unit} $".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{v:,.0f} $"


def _pct(v) -> str:
    return "n/d" if v is None else f"{v:+.1f}%".replace(".", ",")


def metrics_text(m: dict | None) -> str:
    """Una riga in italiano, es. «📑 Bilanci (trimestre al 2026-06-27): ricavi in 12 mesi 466,8 Mld $ …»."""
    if not m:
        return ""
    return (f"📑 Bilanci (trimestre al {m.get('period_end')}): ricavi in 12 mesi {_money(m.get('revenue_ttm'))}, "
            f"ricavi {_pct(m.get('revenue_growth_yoy'))} e utile per azione {_pct(m.get('eps_growth_yoy'))} "
            f"rispetto a un anno prima, margine netto {_pct(m.get('net_margin')).lstrip('+')}")
