"""Recap della giornata, calendario degli errori e statistiche delle operazioni."""

from __future__ import annotations

import calendar as _cal
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from previsioni import CLOSED, STAKE_EUR, compute_stats, load_journal
from webapp import store


def _local_bounds_utc(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min).astimezone()
    return start.astimezone(timezone.utc).isoformat(timespec="seconds"), \
        (start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")


def local_day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d")


def local_hour(iso: str | None) -> int | None:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().hour


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def operations(source: str = "massimi") -> list[dict]:
    return store.load_operations() if source == "massimi" else load_journal()


# --------------------------------------------------------------------------- #
# Recap della giornata
# --------------------------------------------------------------------------- #

def day_recap(day: date) -> dict:
    d = day.strftime("%Y-%m-%d")
    start, end = _local_bounds_utc(day)
    sigs = [s for s in store.signals(since_iso=start, limit=5000) if s["created"] < end]
    pre = [s for s in sigs if s["kind"] == "preavviso"]
    maxs = [s for s in sigs if s["kind"] == "massimo"]
    conf = sum(s["status"] == "confermato" for s in pre)
    canc = sum(s["status"] == "annullato" for s in pre)

    ops = store.load_operations()
    new_ops = [o for o in ops if local_day(o.get("created")) == d]
    closed = [o for o in ops if o.get("status") in CLOSED and local_day(o.get("closed_at")) == d]
    net = [o["net_pct"] for o in closed if _f(o.get("net_pct")) is not None]
    open_ops = [o for o in ops if o.get("status") in ("pending", "open")]

    journal = load_journal()
    t_closed = [o for o in journal if o.get("status") in CLOSED and local_day(o.get("closed_at")) == d]
    t_net = [o["net_pct"] for o in t_closed if _f(o.get("net_pct")) is not None]

    errs = store.errors(day=d)
    best = max(closed, key=lambda o: o.get("net_pct") or -1e9) if closed else None
    worst = min(closed, key=lambda o: o.get("net_pct") or 1e9) if closed else None
    return {
        "day": d,
        "preavvisi": len(pre), "confermati": conf, "annullati": canc,
        "in_attesa": sum(s["status"] == "attesa" for s in pre),
        "precisione_preavvisi": conf / (conf + canc) * 100 if conf + canc else None,
        "massimi": len(maxs),
        "signals": maxs + pre,
        "ops_new": len(new_ops), "ops_closed": len(closed),
        "wins": sum(o["status"] == "win" for o in closed), "losses": sum(o["status"] == "loss" for o in closed),
        "timeouts": sum(o["status"] == "timeout" for o in closed),
        "net_eur": float(sum(net) * STAKE_EUR / 100), "net_mean": float(np.mean(net)) if net else None,
        "best": {"symbol": best["symbol"], "net_pct": best["net_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "net_pct": worst["net_pct"]} if worst else None,
        "closed_list": closed, "open_ops": open_ops,
        "test_closed": len(t_closed), "test_net_eur": float(sum(t_net) * STAKE_EUR / 100),
        "test_wins": sum(o["status"] == "win" for o in t_closed),
        "errors_tech": sum(e["category"] == "tecnico" for e in errs),
        "errors_pred": sum(e["category"] == "previsione" for e in errs),
        "errors": errs,
        "top_coins": [s for s, _ in Counter(s["symbol"] for s in sigs).most_common(5)],
    }


def recap_text(r: dict) -> str:
    lines = [f"Giornata del {datetime.strptime(r['day'], '%Y-%m-%d'):%d/%m/%Y}"]
    lines.append(f"🟢 Segnali BUY massimo: {r['massimi']}"
                 + (f" ({', '.join(sorted({s['symbol'] for s in r['signals'] if s['kind'] == 'massimo'}))})"
                    if r["massimi"] else ""))
    if r["preavvisi"]:
        prec = f" · precisione {r['precisione_preavvisi']:.0f}%" if r["precisione_preavvisi"] is not None else ""
        lines.append(f"⏰ Preavvisi: {r['preavvisi']} → confermati {r['confermati']}, annullati {r['annullati']}{prec}")
    lines.append(f"📈 Operazioni: {r['ops_new']} nuove, {r['ops_closed']} chiuse "
                 f"(✅ {r['wins']} · 🛑 {r['losses']} · ⏱️ {r['timeouts']}) · {len(r['open_ops'])} ancora aperte")
    if r["ops_closed"]:
        lines.append(f"💶 Risultato con {STAKE_EUR}€ per operazione: {r['net_eur']:+.2f}€")
        if r["best"]:
            lines.append(f"🏆 Migliore {r['best']['symbol']} {r['best']['net_pct']:+.2f}% · "
                         f"peggiore {r['worst']['symbol']} {r['worst']['net_pct']:+.2f}%")
    if r["test_closed"]:
        lines.append(f"🧪 Test previsioni: {r['test_closed']} chiuse, {r['test_wins']} al SELL, {r['test_net_eur']:+.2f}€")
    lines.append(f"⚠️ Errori: {r['errors_pred']} di previsione · {r['errors_tech']} tecnici")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Calendario degli errori
# --------------------------------------------------------------------------- #

def month_calendar(month: str) -> dict:
    y, m = (int(x) for x in month.split("-"))
    days: dict = defaultdict(lambda: {"tech": 0, "pred": 0, "wins": 0, "losses": 0, "timeouts": 0, "net_eur": 0.0,
                                      "massimi": 0, "preavvisi": 0, "annullati": 0})
    for e in store.errors(month=month, limit=20000):
        days[e["day"]]["tech" if e["category"] == "tecnico" else "pred"] += 1
    first = date(y, m, 1)
    last = date(y, m, _cal.monthrange(y, m)[1])
    start, _ = _local_bounds_utc(first)
    _, end = _local_bounds_utc(last)
    for s in store.signals(since_iso=start, limit=50000):
        if s["created"] >= end:
            continue
        d = days[local_day(s["created"])]
        if s["kind"] == "massimo":
            d["massimi"] += 1
        elif s["kind"] == "preavviso":
            d["preavvisi"] += 1
            d["annullati"] += s["status"] == "annullato"
    for o in store.load_operations():
        if o.get("status") not in CLOSED:
            continue
        day = local_day(o.get("closed_at"))
        if not day or not day.startswith(month):
            continue
        d = days[day]
        d[{"win": "wins", "loss": "losses", "timeout": "timeouts"}[o["status"]]] += 1
        d["net_eur"] += (_f(o.get("net_pct")) or 0) * STAKE_EUR / 100
    out = {k: v for k, v in days.items() if k and k.startswith(month)}
    tot = {k: sum(v[k] for v in out.values()) for k in ("tech", "pred", "wins", "losses", "massimi", "annullati")}
    tot["net_eur"] = sum(v["net_eur"] for v in out.values())
    return {"month": month, "first_weekday": first.weekday(), "n_days": last.day, "days": out, "totals": tot}


def day_detail(day: str) -> dict:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    rec = day_recap(d)
    return {"day": day, "errors": rec["errors"], "closed": rec["closed_list"], "signals": rec["signals"],
            "recap": {k: v for k, v in rec.items() if k not in ("errors", "closed_list", "signals", "open_ops")}}


# --------------------------------------------------------------------------- #
# Statistiche
# --------------------------------------------------------------------------- #

WEEKDAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


def statistics(source: str = "massimi") -> dict:
    ops = operations(source)
    s = compute_stats(ops)
    out: dict = {"source": source, "summary": s, "stake": STAKE_EUR}
    closed = [o for o in ops if o.get("status") in CLOSED and _f(o.get("net_pct")) is not None]
    closed.sort(key=lambda o: o.get("closed_at") or "")
    cum, equity = 0.0, []
    for o in closed:
        cum += o["net_pct"] * STAKE_EUR / 100
        equity.append({"t": o["closed_at"], "eur": round(cum, 2), "symbol": o["symbol"], "net_pct": o["net_pct"]})
    out["equity"] = equity

    per_coin: dict = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "net_eur": 0.0})
    by_hour = {h: {"n": 0, "wins": 0} for h in range(24)}
    by_wd = {i: {"n": 0, "wins": 0, "net_eur": 0.0} for i in range(7)}
    for o in closed:
        c = per_coin[o["symbol"]]
        c["n"] += 1
        c["wins"] += o["status"] == "win"
        c["losses"] += o["status"] == "loss"
        c["net_eur"] += o["net_pct"] * STAKE_EUR / 100
        h = local_hour(o.get("created"))
        if h is not None:
            by_hour[h]["n"] += 1
            by_hour[h]["wins"] += o["status"] == "win"
        try:
            wd = datetime.fromisoformat(o["created"]).astimezone().weekday()
            by_wd[wd]["n"] += 1
            by_wd[wd]["wins"] += o["status"] == "win"
            by_wd[wd]["net_eur"] += o["net_pct"] * STAKE_EUR / 100
        except (KeyError, ValueError, TypeError):
            pass
    out["per_coin"] = sorted(({"symbol": k, **v, "win_rate": v["wins"] / v["n"] * 100} for k, v in per_coin.items()),
                             key=lambda r: -r["net_eur"])
    out["by_hour"] = [{"hour": h, **v, "win_rate": v["wins"] / v["n"] * 100 if v["n"] else None}
                      for h, v in by_hour.items()]
    out["by_weekday"] = [{"day": WEEKDAYS[i], **v, "win_rate": v["wins"] / v["n"] * 100 if v["n"] else None}
                         for i, v in by_wd.items()]

    calib = []
    for lo in range(0, 100, 10):
        grp = [o for o in closed if lo <= (_f(o.get("probability")) or 0) * 100 < lo + 10]
        if grp:
            calib.append({"bucket": f"{lo}-{lo + 10}%", "n": len(grp),
                          "predicted": float(np.mean([o["probability"] for o in grp]) * 100),
                          "actual": sum(o["status"] == "win" for o in grp) / len(grp) * 100})
    out["calibration"] = calib

    bins = [-100, -5, -3, -2, -1, 0, 1, 2, 3, 5, 1000]
    labels = ["< -5%", "-5/-3", "-3/-2", "-2/-1", "-1/0", "0/+1", "+1/+2", "+2/+3", "+3/+5", "> +5%"]
    if closed:
        cut = pd.cut(pd.Series([o["net_pct"] for o in closed]), bins=bins, labels=labels, right=False)
        counts = cut.value_counts().reindex(labels, fill_value=0)
        out["distribution"] = [{"bin": k, "n": int(v)} for k, v in counts.items()]
    else:
        out["distribution"] = []

    # segnali e preavvisi degli ultimi 30 giorni
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    sigs = store.signals(since_iso=since, limit=20000)
    per_day: dict = defaultdict(lambda: {"massimi": 0, "preavvisi": 0, "confermati": 0, "annullati": 0})
    for x in sigs:
        d = per_day[local_day(x["created"])]
        if x["kind"] == "massimo":
            d["massimi"] += 1
        elif x["kind"] == "preavviso":
            d["preavvisi"] += 1
            d["confermati"] += x["status"] == "confermato"
            d["annullati"] += x["status"] == "annullato"
    out["signals_per_day"] = [{"day": k, **v} for k, v in sorted(per_day.items()) if k]
    conf = sum(v["confermati"] for v in per_day.values())
    canc = sum(v["annullati"] for v in per_day.values())
    out["prealert_precision"] = conf / (conf + canc) * 100 if conf + canc else None
    out["prealert_counts"] = {"confermati": conf, "annullati": canc}
    return out
