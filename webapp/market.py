"""Visione d'insieme del mercato: sentimento, dati globali e ampiezza (quante coin salgono davvero).

Fonti gratuite e senza chiave:
* indice Paura/Avidità di alternative.me (0 = paura estrema, 100 = avidità estrema), aggiornato una volta al giorno;
* dati globali di CoinGecko: capitalizzazione totale, variazione 24 h, dominanza di BTC ed ETH;
* ampiezza calcolata dal radar: percentuale di coin sopra la EMA 200, RSI medio, variazione media 24 h.

I dati esterni sono in cache (`CACHE_SECONDS`): una fonte che non risponde non blocca mai il radar.
"""
from __future__ import annotations

import logging
import threading
import time

from webapp import store

log = logging.getLogger("radar.web.market")

CACHE_SECONDS = 1800
FNG_URL = "https://api.alternative.me/fng/?limit=8"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
TIMEOUT = 10


def _get_json(url: str):
    import requests

    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "radar-web"})
    r.raise_for_status()
    return r.json()


def _cached(key: str, fetch, now: float | None = None):
    """Valore in cache se recente; altrimenti lo riscarica. Se la fonte non risponde tiene l'ultimo valore."""
    now = now or time.time()
    c = store.kv_get(key) or {}
    if c.get("at") and now - c["at"] < CACHE_SECONDS:
        return c.get("data")
    try:
        data = fetch()
    except Exception as e:  # noqa: BLE001 - una fonte esterna non deve mai fermare il radar
        log.info("mercato: %s non disponibile: %s", key, e)
        return c.get("data")
    store.kv_set(key, {"at": now, "data": data})
    return data


def fear_greed(fetch=None) -> dict | None:
    def load():
        rows = (fetch or (lambda: _get_json(FNG_URL)))()["data"]
        vals = [int(r["value"]) for r in rows]
        return {"value": vals[0], "label": rows[0]["value_classification"],
                "week_ago": vals[-1] if len(vals) > 1 else None, "history": vals[::-1]}
    return _cached("market_fng", load)


def global_market(fetch=None) -> dict | None:
    def load():
        d = (fetch or (lambda: _get_json(GLOBAL_URL)))()["data"]
        mc = d.get("market_cap_percentage") or {}
        return {"market_cap_usd": (d.get("total_market_cap") or {}).get("usd"),
                "volume_usd": (d.get("total_volume") or {}).get("usd"),
                "change_24h": d.get("market_cap_change_percentage_24h_usd"),
                "btc_dominance": mc.get("btc"), "eth_dominance": mc.get("eth"),
                "coins": d.get("active_cryptocurrencies")}
    return _cached("market_global", load)


def breadth(rows: list[dict]) -> dict | None:
    """Ampiezza del mercato dalle coin del radar."""
    rows = [r for r in rows or [] if r.get("rsi") is not None]
    if not rows:
        return None
    n = len(rows)
    up = sum(1 for r in rows if r.get("trend_ema") == "Rialzista")
    btc = next((r for r in rows if str(r.get("symbol", "")).startswith("BTC/")), None)
    return {"coins": n, "above_ema200": up, "above_pct": up / n * 100,
            "rsi_avg": sum(float(r["rsi"]) for r in rows) / n,
            "rsi_below_35": sum(1 for r in rows if float(r["rsi"]) < 35),
            "change_24h_avg": sum(float(r.get("change_24h") or 0) for r in rows) / n,
            "rising_24h": sum(1 for r in rows if float(r.get("change_24h") or 0) > 0),
            "btc": {"price": btc.get("price"), "change_24h": btc.get("change_24h"), "rsi": btc.get("rsi"),
                    "trend": btc.get("trend_ema")} if btc else None}


def regime(b: dict | None, fng: dict | None) -> dict:
    """Lettura in parole del momento di mercato e cosa significa per la strategia 'Compra il ribasso'."""
    if not b:
        return {"level": "ignoto", "text": "Dati del radar non ancora disponibili."}
    pct = b["above_pct"]
    if pct >= 60:
        level, text = "rialzo", "Mercato in salita: la maggior parte delle coin è sopra la media a 200 candele."
    elif pct >= 30:
        level, text = "misto", "Mercato misto: solo una parte delle coin è sopra la media a 200 candele."
    else:
        level, text = "ribasso", ("Mercato in discesa: quasi tutte le coin sono sotto la media a 200 candele. "
                                  "La strategia 'Compra il ribasso' resta ferma apposta (niente acquisti contro il trend).")
    if fng and fng.get("value") is not None:
        v = fng["value"]
        if v <= 20:
            text += f" Sentimento: paura estrema ({v}/100)."
        elif v >= 80:
            text += f" Sentimento: avidità estrema ({v}/100), attenzione ai picchi."
    return {"level": level, "text": text}


_REFRESH_LOCK = threading.Lock()


def refresh() -> None:
    fear_greed()
    global_market()


def refresh_async() -> bool:
    """Aggiorna i dati esterni in un thread a parte (mai nel thread che controlla STOP e SELL)."""
    stale = any(time.time() - ((store.kv_get(k) or {}).get("at") or 0) >= CACHE_SECONDS
                for k in ("market_fng", "market_global"))
    if not stale or not _REFRESH_LOCK.acquire(blocking=False):
        return False

    def work():
        try:
            refresh()
        finally:
            _REFRESH_LOCK.release()

    threading.Thread(target=work, name="mercato", daemon=True).start()
    return True


def overview(radar_rows: list[dict] | None = None) -> dict:
    """Solo dati già in cache: la pagina non aspetta mai una fonte esterna."""
    if radar_rows is None:
        radar_rows = (store.kv_get("radar") or {}).get("rows") or []
    b = breadth(radar_rows)
    fng = (store.kv_get("market_fng") or {}).get("data")
    glob = (store.kv_get("market_global") or {}).get("data")
    return {"breadth": b, "fear_greed": fng, "global": glob, "regime": regime(b, fng)}
