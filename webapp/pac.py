"""PAC (piano di accumulo) su ETF: piano, acquisti registrati, valore, tasse italiane, prossimo versamento,
proiezioni e prova sul passato. Nessun ordine automatico: gli acquisti li fai tu dal tuo broker e li registri qui.

Tasse (Italia, regime amministrato, stima): 26% sulla plusvalenza quando vendi (sugli ETF la plusvalenza è
reddito di capitale: le minusvalenze degli ETF non compensano i guadagni degli ETF), imposta di bollo 0,2%
all'anno sul valore. La parte in titoli di Stato di un ETF obbligazionario ha l'aliquota 12,5%: qui non è
considerata (stima prudente al 26%).
"""

from __future__ import annotations

import math
import re
import time
import uuid
from datetime import date

import numpy as np
import pandas as pd

from webapp import store

KV_KEY = "pac"
KV_PRICES = "pac_prices"
TAX_GAIN = 0.26
STAMP_DUTY = 0.002            # imposta di bollo annua sul valore
PRICE_TTL_S = 6 * 3600        # prezzi riusati per 6 ore
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
PROJ_RATES = (0.04, 0.07, 0.10)
PROJ_YEARS = (5, 10, 20, 30)

DEFAULT_PLAN = {"monthly": 200.0, "day": 5, "fees": 0.0,
                "etfs": [{"ticker": "SWDA.MI", "name": "MSCI World (esempio)", "weight": 100.0}]}
EXAMPLES = [  # ETF UCITS ad accumulazione quotati in Europa (esempi, non consigli)
    {"ticker": "SWDA.MI", "name": "iShares Core MSCI World (Borsa Italiana)"},
    {"ticker": "VWCE.DE", "name": "Vanguard FTSE All-World (Xetra)"},
    {"ticker": "CSSPX.MI", "name": "iShares Core S&P 500 (Borsa Italiana)"},
    {"ticker": "EIMI.MI", "name": "iShares Core MSCI EM IMI (Borsa Italiana)"},
    {"ticker": "AGGH.MI", "name": "iShares Core Global Aggregate Bond EUR Hedged (Borsa Italiana)"},
]


class PacError(ValueError):
    """Dato non valido inserito dall'utente (il messaggio è mostrato così com'è)."""


# --------------------------------------------------------------------------- #
# Stato e validazione
# --------------------------------------------------------------------------- #

def load() -> dict:
    st = store.kv_get(KV_KEY) or {}
    return {"plan": st.get("plan") or dict(DEFAULT_PLAN), "buys": list(st.get("buys") or []),
            "reminded": st.get("reminded")}


def save(st: dict) -> None:
    store.kv_set(KV_KEY, st)


def _num(v, name: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise PacError(f"{name}: serve un numero") from None
    if not math.isfinite(x) or not lo <= x <= hi:
        raise PacError(f"{name}: deve stare tra {lo:g} e {hi:g}")
    return x


def ticker(v) -> str:
    t = str(v or "").strip().upper()
    if not TICKER_RE.match(t):
        raise PacError(f"Ticker non valido: {v!r} (esempi: SWDA.MI, VWCE.DE)")
    return t


def clean_plan(body: dict) -> dict:
    etfs, seen = [], set()
    for e in body.get("etfs") or []:
        t = ticker(e.get("ticker"))
        if t in seen:
            raise PacError(f"{t} è ripetuto")
        seen.add(t)
        etfs.append({"ticker": t, "name": str(e.get("name") or "")[:80],
                     "weight": _num(e.get("weight"), f"Peso di {t}", 0.01, 100)})
    if not etfs:
        raise PacError("Serve almeno un ETF")
    if len(etfs) > 10:
        raise PacError("Massimo 10 ETF")
    tot = sum(e["weight"] for e in etfs)
    for e in etfs:  # pesi riportati a 100
        e["weight"] = round(e["weight"] / tot * 100, 2)
    return {"monthly": _num(body.get("monthly"), "Versamento mensile", 1, 100_000),
            "day": int(_num(body.get("day"), "Giorno del mese", 1, 28)),
            "fees": _num(body.get("fees", 0), "Commissione per acquisto", 0, 100), "etfs": etfs}


def clean_buy(body: dict, today: date | None = None) -> dict:
    today = today or date.today()
    try:
        d = date.fromisoformat(str(body.get("date") or ""))
    except ValueError:
        raise PacError("Data non valida (formato AAAA-MM-GG)") from None
    if d > today:
        raise PacError("La data non può essere nel futuro")
    if d.year < 1990:
        raise PacError("Data troppo vecchia")
    return {"id": uuid.uuid4().hex[:12], "date": d.isoformat(), "ticker": ticker(body.get("ticker")),
            "qty": _num(body.get("qty"), "Quote", 1e-6, 1e9), "price": _num(body.get("price"), "Prezzo", 1e-6, 1e7),
            "fees": _num(body.get("fees", 0), "Commissioni", 0, 10_000)}


# --------------------------------------------------------------------------- #
# Prezzi (yfinance), con cache
# --------------------------------------------------------------------------- #

def yf_history(tickers: list[str], start: str) -> dict[str, pd.Series]:
    import yfinance as yf

    raw = yf.download(tickers, start=start, auto_adjust=False, progress=False, group_by="ticker", threads=True)
    return closes_from(raw, tickers)


def closes_from(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.Series]:
    """Chiusure per ticker dal risultato di yf.download, sia con colonne (ticker, campo) sia piatte."""
    out = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            s = raw[t]["Close"] if multi else raw["Close"]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            s = s.dropna()
        except (KeyError, IndexError):
            continue
        if len(s):
            s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
            out[t] = s.astype(float)
    return out


def prices(tickers: list[str], start: str, fetch=None, force: bool = False) -> tuple[dict[str, pd.Series], list[str]]:
    """Chiusure giornaliere da `start`. Ritorna (serie, ticker senza dati)."""
    tickers = sorted(set(tickers))
    cache = store.kv_get(KV_PRICES) or {}
    fresh = (not force and cache.get("start", "9999") <= start and set(tickers) <= set(cache.get("series", {}))
             and time.time() - float(cache.get("at") or 0) < PRICE_TTL_S)
    if not fresh and tickers:
        try:
            got = (fetch or yf_history)(tickers, start)
        except Exception as e:  # noqa: BLE001 - senza rete si usano i prezzi salvati
            store.add_error("tecnico", "pac", f"Prezzi ETF non scaricati: {type(e).__name__}: {e}")
            got = {}
        if got:
            series = {**cache.get("series", {}),
                      **{t: {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()} for t, s in got.items()}}
            cache = {"at": time.time(), "start": start, "series": series}
            store.kv_set(KV_PRICES, cache)
    out = {}
    for t in tickers:
        raw = (cache.get("series") or {}).get(t)
        if raw:
            out[t] = pd.Series(list(raw.values()), index=pd.DatetimeIndex(list(raw.keys()))).sort_index()
    return out, [t for t in tickers if t not in out]


# --------------------------------------------------------------------------- #
# Calcoli
# --------------------------------------------------------------------------- #

def positions(buys: list[dict], last: dict[str, float]) -> list[dict]:
    rows: dict[str, dict] = {}
    for b in buys:
        r = rows.setdefault(b["ticker"], {"ticker": b["ticker"], "qty": 0.0, "cost": 0.0, "buys": 0})
        r["qty"] += b["qty"]
        r["cost"] += b["qty"] * b["price"] + b.get("fees", 0)
        r["buys"] += 1
    for r in rows.values():
        px = last.get(r["ticker"])
        r["avg_price"] = r["cost"] / r["qty"] if r["qty"] else None
        r["price"] = px
        r["value"] = r["qty"] * px if px is not None else None
        r["gain"] = r["value"] - r["cost"] if r["value"] is not None else None
        r["gain_pct"] = r["gain"] / r["cost"] * 100 if r["gain"] is not None and r["cost"] else None
    return sorted(rows.values(), key=lambda r: -(r["value"] or r["cost"]))


def taxes(cost: float, value: float, years_held: float) -> dict:
    gain = value - cost
    tax = max(gain, 0) * TAX_GAIN
    stamp = value * STAMP_DUTY
    return {"gain": gain, "tax_if_sold": tax, "stamp_year": stamp, "net_if_sold": value - tax,
            "net_gain_if_sold": gain - tax, "years": years_held}


def curve(buys: list[dict], px: dict[str, pd.Series]) -> pd.DataFrame:
    """Valore e capitale versato giorno per giorno; rendimento 'time-weighted' (senza l'effetto dei versamenti)."""
    if not buys or not px:
        return pd.DataFrame(columns=["value", "invested", "index"])
    start = pd.Timestamp(min(b["date"] for b in buys))
    days = sorted({d for s in px.values() for d in s.index if d >= start})
    if not days:
        return pd.DataFrame(columns=["value", "invested", "index"])
    idx = pd.DatetimeIndex(days)
    close = pd.DataFrame({t: s.reindex(idx.union(s.index)).ffill().reindex(idx) for t, s in px.items()})
    qty = pd.DataFrame(0.0, index=idx, columns=close.columns)
    flows = pd.Series(0.0, index=idx)
    for b in buys:
        d = idx[idx.searchsorted(pd.Timestamp(b["date"]))] if pd.Timestamp(b["date"]) <= idx[-1] else None
        if d is None or b["ticker"] not in qty:
            continue
        qty.loc[d:, b["ticker"]] += b["qty"]
        flows[d] += b["qty"] * b["price"] + b.get("fees", 0)
    value = (qty * close.fillna(0)).sum(axis=1)
    invested = flows.cumsum()
    # indice: rendimento giornaliero = (valore oggi - versato oggi) / valore ieri
    prev = value.shift()
    r = ((value - flows) / prev - 1).where(prev > 0, 0.0).replace([np.inf, -np.inf], 0).fillna(0)
    return pd.DataFrame({"value": value, "invested": invested, "index": (1 + r).cumprod()})


def max_drawdown(s: pd.Series) -> float:
    if s is None or not len(s):
        return 0.0
    return float((1 - s / s.cummax()).max() * 100)


def next_contribution(plan: dict, buys: list[dict], pos: list[dict], last: dict[str, float],
                      today: date | None = None) -> dict:
    """Quanto comprare di ogni ETF questo mese: il versamento va dove si è sotto il peso obiettivo
    (si ribilancia comprando, senza vendere e quindi senza pagare tasse)."""
    today = today or date.today()
    month = today.strftime("%Y-%m")
    due = date(today.year, today.month, int(plan["day"]))
    done = sum(b["qty"] * b["price"] + b.get("fees", 0) for b in buys if b["date"][:7] == month)
    amount = float(plan["monthly"])
    vals = {e["ticker"]: next((p["value"] or p["cost"] for p in pos if p["ticker"] == e["ticker"]), 0.0)
            for e in plan["etfs"]}
    total = sum(vals.values()) + amount
    deficit = {t: max(next(e["weight"] for e in plan["etfs"] if e["ticker"] == t) / 100 * total - v, 0)
               for t, v in vals.items()}
    dsum = sum(deficit.values())
    rows = []
    for e in plan["etfs"]:
        t = e["ticker"]
        eur = amount * (deficit[t] / dsum if dsum > 0 else e["weight"] / 100)
        px = last.get(t)
        cur_w = vals[t] / (total - amount) * 100 if total - amount > 0 else 0.0
        rows.append({"ticker": t, "name": e["name"], "target_weight": e["weight"], "weight_now": cur_w,
                     "eur": eur, "price": px, "qty": eur / px if px else None,
                     "whole_qty": math.floor(eur / px) if px else None})
    status = "fatto" if done >= amount * 0.9 else ("da fare" if today >= due else "in arrivo")
    return {"month": month, "due": due.isoformat(), "amount": amount, "done_eur": done, "status": status,
            "rows": rows}


def projection(monthly: float, start_value: float = 0.0) -> list[dict]:
    """Valore futuro con versamento mensile costante e rendimento annuo fisso (prima delle tasse)."""
    out = []
    for y in PROJ_YEARS:
        row = {"years": y, "invested": start_value + monthly * 12 * y}
        for r in PROJ_RATES:
            m = (1 + r) ** (1 / 12) - 1
            n = 12 * y
            fv = start_value * (1 + m) ** n + monthly * (((1 + m) ** n - 1) / m)
            gain = fv - row["invested"]
            row[f"r{int(r * 100)}"] = fv
            row[f"r{int(r * 100)}_net"] = fv - max(gain, 0) * TAX_GAIN
        out.append(row)
    return out


def backtest(series: pd.Series, monthly: float, since: int) -> dict | None:
    """Se avessi fatto il PAC su questo ETF dall'anno `since`: un acquisto al primo giorno di borsa di ogni mese."""
    s = series[series.index >= pd.Timestamp(f"{since}-01-01")]
    if len(s) < 40:
        return None
    firsts = s.groupby(s.index.to_period("M")).head(1)
    qty, inv, worst, pts = 0.0, 0.0, None, []
    buy_days = set(firsts.index)
    for d, p in s.items():
        if d in buy_days:
            qty += monthly / p
            inv += monthly
        v = qty * p
        loss = v - inv  # il momento con la perdita più grande in euro rispetto al versato
        if worst is None or loss < worst["loss"]:
            worst = {"date": d.strftime("%Y-%m-%d"), "loss": loss, "ratio": v / inv - 1 if inv else 0.0,
                     "value": v, "invested": inv}
        if d in buy_days:
            pts.append({"t": d.strftime("%Y-%m-%d"), "v": round(v, 2), "inv": round(inv, 2)})
    final = qty * float(s.iloc[-1])
    years = (s.index[-1] - s.index[0]).days / 365.25
    idx = s / s.iloc[0]
    return {"since": since, "from": s.index[0].strftime("%Y-%m-%d"), "to": s.index[-1].strftime("%Y-%m-%d"),
            "years": round(years, 1), "invested": inv, "value": final, "gain": final - inv,
            "gain_pct": (final / inv - 1) * 100 if inv else None,
            "etf_cagr": (float(idx.iloc[-1]) ** (1 / years) - 1) * 100 if years > 0.5 else None,
            "etf_max_drawdown": max_drawdown(idx), "worst": worst, "curve": pts}


def summary(st: dict | None = None, fetch=None, today: date | None = None, force: bool = False) -> dict:
    st = st or load()
    today = today or date.today()
    plan, buys = st["plan"], st["buys"]
    tick = sorted({e["ticker"] for e in plan["etfs"]} | {b["ticker"] for b in buys})
    first = min([b["date"] for b in buys], default=None)
    start = min(first or today.isoformat(), f"{today.year - 16}-01-01")
    px, missing = prices(tick, start, fetch=fetch, force=force)
    last = {t: float(s.iloc[-1]) for t, s in px.items() if len(s)}
    last_date = {t: s.index[-1].strftime("%Y-%m-%d") for t, s in px.items() if len(s)}
    pos = positions(buys, last)
    cost = sum(p["cost"] for p in pos)
    priced = all(p["value"] is not None for p in pos)
    value = sum(p["value"] or 0 for p in pos) if priced else None
    years = (today - date.fromisoformat(first)).days / 365.25 if first else 0.0
    cv = curve(buys, {t: px[t] for t in {b["ticker"] for b in buys} if t in px})
    tw = float(cv["index"].iloc[-1] - 1) * 100 if len(cv) else None
    out = {"plan": plan, "buys": sorted(buys, key=lambda b: b["date"], reverse=True), "positions": pos,
           "missing": missing, "last_date": last_date, "examples": EXAMPLES,
           "totals": {"cost": cost, "value": value, "buys": len(buys), "first": first,
                      "twr_pct": tw, "max_drawdown": max_drawdown(cv["index"]) if len(cv) else 0.0,
                      **(taxes(cost, value, years) if value is not None else {})},
           "curve": [{"t": d.strftime("%Y-%m-%d"), "v": round(float(r.value), 2), "inv": round(float(r.invested), 2)}
                     for d, r in cv.iloc[::max(1, len(cv) // 400)].iterrows()] if len(cv) else [],
           "next": next_contribution(plan, buys, pos, last, today),
           "projection": projection(float(plan["monthly"]), value or 0.0)}
    main = plan["etfs"][0]["ticker"]
    bt = []
    if main in px:
        for since in (today.year - 15, today.year - 10, today.year - 5):
            r = backtest(px[main], float(plan["monthly"]), since)
            if r:
                bt.append(r)
    out["backtest"] = {"ticker": main, "runs": bt}
    return out


# --------------------------------------------------------------------------- #
# Promemoria mensile (chiamato dal motore ogni minuto)
# --------------------------------------------------------------------------- #

def reminder(notify, cfg: dict, today: date | None = None) -> bool:
    """Il giorno del versamento (o dopo), se nel mese non c'è ancora un acquisto: un avviso, una volta al mese."""
    if not (store.kv_get(KV_KEY) or {}).get("plan"):
        return False  # PAC mai configurato
    st = load()
    today = today or date.today()
    month = today.strftime("%Y-%m")
    plan = st["plan"]
    if today.day < int(plan["day"]) or st.get("reminded") == month:
        return False
    if any(b["date"][:7] == month for b in st["buys"]):
        return False
    lines = "\n".join(f"• {e['ticker']}: {e['weight']:g}%" for e in plan["etfs"])
    notify(cfg, "info", "💶 PAC: è il giorno del versamento",
           f"Questo mese: {plan['monthly']:.2f} € nel piano.\n{lines}\n"
           "Apri Radar Web → PAC per vedere quante quote comprare, poi registra l'acquisto.",
           f"PAC: versa {plan['monthly']:.0f} € questo mese")
    st["reminded"] = month
    save(st)
    return True
