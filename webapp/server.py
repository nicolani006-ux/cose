"""Server locale di Radar Web (Starlette + uvicorn, già inclusi con Streamlit: nessuna dipendenza nuova).

Ascolta solo su 127.0.0.1. Le richieste che modificano qualcosa (POST) devono avere l'intestazione
``X-Radar: 1``: un sito esterno aperto nel browser non può aggiungerla senza permesso (CORS), quindi
non può cambiare le impostazioni o avviare il bot. Anche l'Host viene controllato (DNS rebinding).
"""

from __future__ import annotations

import csv
import io
import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from stocks import background
from stocks.config import DEFAULT_CONFIG, load_config, pairing_code, save_config
from webapp import autotrade, engine, lab, market, pac, reports, store

log = logging.getLogger("radar.web")
STATIC = Path(__file__).resolve().parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# impostazioni modificabili dalla pagina, con il tipo
SETTINGS: dict[str, type] = {
    "strategia": str, "dip_rsi": float, "dip_stop_atr": float, "dip_target_atr": float, "dip_candele_max": int, "rimb_banda": float, "rimb_stop_atr": float, "rimb_candele_max": int,
    "segnali_attivi": bool, "segnali_exchange": str, "segnali_quote": str, "segnali_timeframe": str,
    "segnali_coins": int, "segnali_volume_min": float, "auto_spread_max": float, "preavviso_minuti": float, "soglia_massimo": float, "soglia_preavviso": float,
    "probabilita_minima": float, "pausa_stesso_simbolo_ore": float, "notifica_preavviso": bool,
    "riepilogo_giornaliero": bool, "riepilogo_ora": str, "notifiche_desktop": bool, "capitale": float,
    "rischio_max_operazione": float, "posizione_max_pct": float, "riserva_cassa_pct": float, "ordine_max": float,
    "test_previsioni": bool, "livello_minimo_avviso": str, "ore_silenzio": str, "web_porta": int,
}
CHOICES = {
    # bybiteu / myokx / kraken / bitvavo / coinbase / cryptocom: entità con licenza MiCA per i clienti UE
    "segnali_exchange": ["bybiteu", "myokx", "kraken", "bitvavo", "coinbase", "cryptocom",
                         "binance", "bybit", "okx", "kucoin", "gateio", "mexc"],
    "segnali_quote": ["USDC", "EUR", "USDT"],
    "segnali_timeframe": ["15m", "1h", "4h"],
    "strategia": ["dip", "trend", "rimbalzo"],
    "livello_minimo_avviso": ["info", "media", "alta"],
}
LIMITS = {"dip_rsi": (10, 50), "dip_stop_atr": (0.5, 5), "dip_target_atr": (0.5, 10), "dip_candele_max": (6, 240), "rimb_banda": (2, 5), "rimb_stop_atr": (0.5, 5), "rimb_candele_max": (6, 240), "segnali_coins": (5, 150), "segnali_volume_min": (0, 10_000_000), "auto_spread_max": (0.05, 5), "preavviso_minuti": (1, 120), "soglia_massimo": (20, 100),
          "soglia_preavviso": (10, 100), "probabilita_minima": (0, 100), "pausa_stesso_simbolo_ore": (0, 168),
          "web_porta": (1024, 65535)}


# --------------------------------------------------------------------------- #
# Utilità
# --------------------------------------------------------------------------- #

def clean(o):
    """Rende serializzabile in JSON: NaN/inf -> None, numpy -> python, date -> stringa."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, bool) or o is None or isinstance(o, str):
        return o
    if hasattr(o, "dtype") and str(o.dtype) == "bool":
        return bool(o)
    if isinstance(o, (int,)):
        return int(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    try:
        f = float(o)
    except (TypeError, ValueError):
        return str(o)
    if not math.isfinite(f):
        return None
    return int(f) if hasattr(o, "dtype") and "int" in str(o.dtype) else f


def ok(data=None, status: int = 200) -> JSONResponse:
    return JSONResponse(clean(data if data is not None else {"ok": True}), status_code=status)


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _today() -> date:
    return datetime.now().date()


def _parse_day(s: str | None) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else _today()
    except ValueError:
        return _today()


# --------------------------------------------------------------------------- #
# Pagine e API
# --------------------------------------------------------------------------- #

def index(request: Request):
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def overview(request: Request):
    cfg = load_config()
    st = engine.status()
    rec = reports.day_recap(_today())
    radar = store.kv_get("radar", {}) or {}
    watch = store.kv_get("watch", {}) or {}
    ops = store.load_operations()
    open_ops = [o for o in ops if o.get("status") in ("pending", "open")]
    today_sigs = [s for s in rec["signals"]]
    stats = reports.compute_stats(ops)
    bot = background.read_status()
    return ok({
        "now": int(time.time() * 1000),
        "engine": st,
        "config": {k: cfg.get(k) for k in ("strategia", "dip_rsi", "segnali_timeframe", "segnali_exchange", "preavviso_minuti",
                                           "soglia_massimo", "soglia_preavviso", "probabilita_minima",
                                           "segnali_attivi")},
        "today": {k: v for k, v in rec.items() if k not in ("signals", "errors", "closed_list", "open_ops")},
        "signals_today": today_sigs[:30],
        "open_ops": open_ops,
        "radar": {"at": radar.get("at"), "candle_close": radar.get("candle_close"),
                  "rows": (radar.get("rows") or [])[:12]},
        "watch": watch,
        "totals": {k: stats.get(k) for k in ("total", "closed", "wins", "losses", "win_rate", "total_eur",
                                             "net_mean", "profit_factor", "verdict")},
        "bot": {"running": background.is_running(), "telegram": bot.get("telegram"),
                "last_contact": bot.get("ultimo_contatto"), "has_token": bool(cfg.get("telegram_token")),
                "linked": bool(cfg.get("telegram_chat_id"))},
        "events": store.events(limit=20),
        "last_event": store.last_event_id(),
        "auto": {"active": bool(cfg.get("auto_attivo")), "mode": cfg.get("auto_modalita"),
                 **autotrade.today_stats(cfg.get("auto_modalita", "simulazione")),
                 "value": autotrade.wallet(cfg.get("auto_modalita", "simulazione"))["value"]},
        "lab": _lab_brief(),
        "market": market.overview(radar.get("rows") or []),
    })


def _lab_brief() -> dict:
    res = store.kv_get(lab.KV_KEY) or {}
    det = res.get("detail") or {}
    return {"running": lab.is_running(), "generated_at": res.get("generated_at"), "current": res.get("current"),
            "verified": res.get("verified"), "summary": det.get("summary"), "quote": res.get("quote")}


# -- mercato ---------------------------------------------------------------- #
def market_get(request: Request):
    return ok(market.overview())


# -- PAC (piano di accumulo su ETF) ------------------------------------------ #
def pac_get(request: Request):
    return ok(pac.summary(force=request.query_params.get("refresh") == "1"))


async def pac_plan(request: Request):
    try:
        plan = pac.clean_plan(await request.json() or {})
    except pac.PacError as e:
        return err(str(e))
    st = pac.load()
    st["plan"] = plan
    pac.save(st)
    return ok({"plan": plan})


async def pac_buy(request: Request):
    try:
        buy = pac.clean_buy(await request.json() or {})
    except pac.PacError as e:
        return err(str(e))
    st = pac.load()
    st["buys"].append(buy)
    pac.save(st)
    return ok({"buy": buy})


async def pac_buy_delete(request: Request):
    bid = str((await request.json() or {}).get("id") or "")
    st = pac.load()
    left = [b for b in st["buys"] if b["id"] != bid]
    if len(left) == len(st["buys"]):
        return err("Acquisto non trovato", 404)
    st["buys"] = left
    pac.save(st)
    return ok()


# -- laboratorio ------------------------------------------------------------ #
def lab_get(request: Request):
    return ok({**(store.kv_get(lab.KV_KEY) or {}), "running": lab.is_running(),
               "refresh_hours": lab.REFRESH_HOURS})


async def lab_run(request: Request):
    started = lab.start(force=True)
    return ok({"started": started, "running": lab.is_running()})


async def lab_auto(request: Request):
    body = await request.json() or {}
    cfg = load_config()
    cfg["lab_auto_applica"] = bool(body.get("active"))
    save_config(cfg)
    store.add_event("info", "Laboratorio: applicazione automatica " + ("attivata" if cfg["lab_auto_applica"] else
                    "disattivata"), "La strategia verificata migliore viene applicata da sola." if
                    cfg["lab_auto_applica"] else "Le impostazioni dei segnali restano quelle scelte da te.")
    return ok({"active": cfg["lab_auto_applica"]})


def radar(request: Request):
    return ok(store.kv_get("radar", {}) or {})


def signals(request: Request):
    days = int(request.query_params.get("days", 7))
    kind = request.query_params.get("kind") or None
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return ok({"signals": store.signals(since_iso=since, kind=kind, limit=2000)})


def operations(request: Request):
    source = request.query_params.get("source", "massimi")
    ops = reports.operations(source)
    ops = sorted(ops, key=lambda o: o.get("created") or "", reverse=True)
    return ok({"operations": ops, "summary": reports.compute_stats(reports.operations(source))})


def stats(request: Request):
    return ok(reports.statistics(request.query_params.get("source", "massimi")))


def recap(request: Request):
    rec = reports.day_recap(_parse_day(request.query_params.get("day")))
    rec["text"] = reports.recap_text(rec)
    return ok(rec)


def calendar(request: Request):
    month = request.query_params.get("month") or datetime.now().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        return err("mese non valido (AAAA-MM)")
    return ok(reports.month_calendar(month))


def day(request: Request):
    return ok(reports.day_detail(_parse_day(request.query_params.get("day")).strftime("%Y-%m-%d")))


def events(request: Request):
    after = int(request.query_params.get("after", 0) or 0)
    return ok({"events": store.events(after=after), "last": store.last_event_id()})


def errors_list(request: Request):
    return ok({"errors": store.errors(limit=int(request.query_params.get("limit", 300)))})


def coin(request: Request):
    """Candele per il grafico (anche quella in corso) + piano dall'ultima scansione."""
    import crypto_core

    cfg = load_config()
    sym = request.query_params.get("symbol", "")
    if not sym or "/" not in sym:
        return err("simbolo mancante, es. BTC/USDT")
    ex, tf = cfg["segnali_exchange"], cfg["segnali_timeframe"]
    try:
        df = crypto_core.fetch_ohlcv(ex, sym, tf, closed_only=False).tail(200)
    except Exception as e:  # noqa: BLE001
        return err(f"Dati non disponibili: {e}", 502)
    candles = [{"time": int(t.value // 1_000_000_000), "open": o, "high": h, "low": lo, "close": c}
               for t, o, h, lo, c in zip(df["time"], df["open"], df["high"], df["low"], df["close"])]
    row = next((r for r in (store.kv_get("radar", {}) or {}).get("rows", []) if r["symbol"] == sym), None)
    hist = [s for s in store.signals(limit=5000) if s["symbol"] == sym][:50]
    return ok({"symbol": sym, "timeframe": tf, "candles": candles, "plan": row, "signals": hist})


# -- impostazioni ------------------------------------------------------------ #
def settings_get(request: Request):
    cfg = load_config()
    out = {k: cfg.get(k, DEFAULT_CONFIG.get(k)) for k in SETTINGS}
    out["has_token"] = bool(cfg.get("telegram_token"))
    out["chat_linked"] = bool(cfg.get("telegram_chat_id"))
    return ok({"settings": out, "choices": CHOICES, "limits": LIMITS})


async def settings_post(request: Request):
    body = await request.json()
    cfg = load_config()
    changed = []
    for k, v in (body or {}).items():
        if k not in SETTINGS:
            continue
        t = SETTINGS[k]
        try:
            v = (v in (True, "true", "1", 1, "on")) if t is bool else t(v)
        except (TypeError, ValueError):
            return err(f"valore non valido per {k}")
        if k in CHOICES and v not in CHOICES[k]:
            return err(f"valore non ammesso per {k}")
        if k in LIMITS:
            lo, hi = LIMITS[k]
            if not lo <= v <= hi:
                return err(f"{k} deve stare tra {lo} e {hi}")
        if k == "riepilogo_ora":
            try:
                datetime.strptime(v, "%H:%M")
            except ValueError:
                return err("ora del recap non valida (HH:MM)")
        if cfg.get(k) != v:
            cfg[k] = v
            changed.append(k)
    if changed:
        save_config(cfg)
        store.add_event("info", "Impostazioni salvate", ", ".join(changed))
    return ok({"changed": changed})


# -- investimento automatico ------------------------------------------------ #
AUTO_SETTINGS: dict[str, tuple] = {  # chiave: (tipo, minimo, massimo)
    "auto_percentuale": (float, 1, 100), "auto_importo_max": (float, 0, 1_000_000),
    "auto_importo_min": (float, 5, 1_000_000), "auto_max_trade_giorno": (int, 1, 50),
    "auto_max_posizioni": (int, 1, 20), "auto_stop_perdita_giorno": (float, 0, 1_000_000),
    "auto_riserva": (float, 0, 10_000_000), "auto_solo_preavvisati": (bool, None, None),
    "auto_piano_importo": (float, 0, 1_000_000), "auto_piano_frequenza": (str, None, None),
    "auto_piano_giorno": (int, 1, 28), "auto_spread_max": (float, 0, 5),
}
CONFIRM_REAL = "INVESTI"


def auto_get(request: Request):
    cfg = load_config()
    mode = request.query_params.get("mode") or cfg.get("auto_modalita", "simulazione")
    if mode not in autotrade.MODES:
        return err("modalità non valida")
    st = autotrade.status(cfg, live_balance=request.query_params.get("live") != "0")
    return ok({"status": st, "history": autotrade.history(mode), "view_mode": mode,
               "settings": {k: cfg.get(k, DEFAULT_CONFIG.get(k)) for k in AUTO_SETTINGS},
               "limits": {k: [lo, hi] for k, (_, lo, hi) in AUTO_SETTINGS.items() if lo is not None},
               "quote": cfg.get("segnali_quote"), "exchange": cfg.get("segnali_exchange"),
               "engine_alive": engine.status().get("alive"), "confirm_word": CONFIRM_REAL})


async def auto_settings(request: Request):
    body = await request.json() or {}
    cfg = load_config()
    changed = []
    for k, v in body.items():
        if k not in AUTO_SETTINGS:
            continue
        t, lo, hi = AUTO_SETTINGS[k]
        try:
            v = (v in (True, "true", "1", 1, "on")) if t is bool else t(v)
        except (TypeError, ValueError):
            return err(f"valore non valido per {k}")
        if isinstance(v, float) and not math.isfinite(v):
            return err(f"valore non valido per {k}")
        if lo is not None and not lo <= v <= hi:
            return err(f"{k} deve stare tra {lo} e {hi}")
        if k == "auto_piano_frequenza" and v not in ("settimanale", "mensile"):
            return err("frequenza non valida")
        if cfg.get(k) != v:
            cfg[k] = v
            changed.append(k)
    if cfg.get("auto_piano_frequenza") == "settimanale" and int(cfg.get("auto_piano_giorno") or 1) > 7:
        return err("per il piano settimanale il giorno va da 1 (lunedì) a 7 (domenica)")
    if changed:
        save_config(cfg)
        store.add_event("auto", "Regole dell'investimento salvate", ", ".join(changed))
    return ok({"changed": changed})


async def auto_active(request: Request):
    body = await request.json() or {}
    cfg = load_config()
    on = bool(body.get("active"))
    if on and cfg.get("auto_modalita") == "reale" and not autotrade.keys_public().get("verified"):
        return err("collega e verifica prima le chiavi API dell'exchange")
    if on and not cfg.get("segnali_attivi", True):
        return err("il motore dei segnali è disattivato (Impostazioni)")
    cfg["auto_attivo"] = on
    save_config(cfg)
    tag = "REALE 💶" if cfg.get("auto_modalita") == "reale" else "simulazione 🧪"
    store.add_event("auto", f"Investimento automatico {'ATTIVATO' if on else 'fermato'} ({tag})",
                    "I prossimi segnali BUY massimo verranno comprati automaticamente." if on else
                    "Nessun nuovo acquisto. Le operazioni aperte restano protette da SELL e STOP.")
    return ok({"active": on})


async def auto_mode(request: Request):
    body = await request.json() or {}
    mode = body.get("mode")
    if mode not in autotrade.MODES:
        return err("modalità non valida")
    cfg = load_config()
    if mode == "reale":
        if not autotrade.keys_public().get("verified"):
            return err("collega e verifica prima le chiavi API dell'exchange")
        if str(body.get("confirm", "")).strip().upper() != CONFIRM_REAL:
            return err(f"per usare soldi veri scrivi {CONFIRM_REAL} nella casella di conferma")
    if cfg.get("auto_modalita") != mode:
        cfg["auto_modalita"] = mode
        cfg["auto_attivo"] = False  # cambiando modalità si riparte da fermo: lo riattivi tu
        save_config(cfg)
        store.add_event("auto", f"Modalità investimento: {mode.upper()}",
                        "Investimento automatico fermo: riattivalo quando sei pronto.")
    return ok({"mode": mode})


async def auto_stop(request: Request):
    """Pulsante di emergenza: ferma i nuovi acquisti e, se richiesto, vende tutto."""
    body = await request.json() or {}
    cfg = load_config()
    cfg["auto_attivo"] = False
    save_config(cfg)
    close = bool(body.get("close_all"))
    if close:
        autotrade.request_close_all(cfg.get("auto_modalita", "simulazione"))
    store.add_event("errore", "⛔ Investimento automatico FERMATO",
                    "Ordini annullati e posizioni vendute al prezzo di mercato (entro 15 secondi)." if close else
                    "Nessun nuovo acquisto. Le operazioni aperte restano protette da SELL e STOP.")
    return ok({"stopped": True, "close_all": close, "engine_alive": engine.status().get("alive")})


async def auto_deposit(request: Request):
    body = await request.json() or {}
    cfg = load_config()
    mode = body.get("mode") or cfg.get("auto_modalita", "simulazione")
    if mode not in autotrade.MODES:
        return err("modalità non valida")
    if body.get("delete"):
        store.delete_deposit(int(body["delete"]))
        return ok({"deleted": True})
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return err("importo non valido")
    if not math.isfinite(amount) or amount == 0 or abs(amount) > 10_000_000:
        return err("importo non valido")
    note = str(body.get("note") or "")[:120]
    store.add_deposit(mode, amount, note or ("prelievo" if amount < 0 else "versamento"))
    store.add_event("auto", f"💶 {'Versamento' if amount > 0 else 'Prelievo'} registrato ({mode})",
                    f"{amount:+.2f} {cfg.get('segnali_quote', 'USDT')}" + (f" · {note}" if note else ""))
    return ok({"amount": amount})


async def auto_keys(request: Request):
    body = await request.json() or {}
    cfg = load_config()
    if body.get("delete"):
        if store.positions("reale", autotrade.OPEN):
            return JSONResponse({"error": "ci sono operazioni reali aperte: chiudile prima di cancellare le chiavi "
                                          "(senza chiavi il radar non potrebbe più controllare STOP e SELL)"},
                                status_code=409)
        autotrade.delete_keys()
        store.kv_set("auto_keys_rev", time.time())
        if cfg.get("auto_modalita") == "reale":
            cfg.update(auto_modalita="simulazione", auto_attivo=False)
            save_config(cfg)
        store.add_event("auto", "Chiavi API eliminate", "Investimento tornato in simulazione.")
        return ok({"deleted": True})
    key, sec = str(body.get("key") or "").strip(), str(body.get("secret") or "").strip()
    pwd = str(body.get("password") or "").strip()
    if not key or not sec:
        return err("inserisci API key e secret")
    ex = cfg.get("segnali_exchange", "binance")
    try:
        info = autotrade.verify_keys(ex, key, sec, pwd, cfg.get("segnali_quote", "USDT"))
    except ValueError as e:
        return err(str(e))
    autotrade.save_keys(ex, key, sec, pwd, info)
    store.kv_set("auto_keys_rev", time.time())
    store.add_event("auto", "🔑 Chiavi API verificate", f"{ex}: saldo libero {info['free']:.2f} "
                    f"{cfg.get('segnali_quote', 'USDT')}")
    return ok({"keys": autotrade.keys_public(), "free": info["free"]})


# -- bot telegram ----------------------------------------------------------- #
def bot_status(request: Request):
    cfg = load_config()
    user = None
    if cfg.get("telegram_token"):
        user = _bot_username(cfg["telegram_token"])
    return ok({
        "running": background.is_running(), "starting": background.starting(),
        "autostart": background.autostart_enabled(), "status": background.read_status(),
        "has_token": bool(cfg.get("telegram_token")), "username": user,
        "linked": bool(cfg.get("telegram_chat_id")),
        "link": f"https://t.me/{user}?start={pairing_code(cfg)}" if user else None,
        "log": background.log_tail(60), "command": background.command_line(),
        "engine": engine.status(),
    })


_USERNAMES: dict = {}


def _bot_username(token: str) -> str | None:
    hit = _USERNAMES.get(token)
    if hit and time.time() - hit[1] < 300:
        return hit[0]
    try:
        from stocks.telegram_bot import TelegramAPI

        name = TelegramAPI(token).call("getMe", http_timeout=10).get("username")
    except Exception:  # noqa: BLE001
        name = None
    _USERNAMES[token] = (name, time.time())
    return name


async def bot_token(request: Request):
    body = await request.json()
    token = str(body.get("token", "")).strip()
    cfg = load_config()
    cfg["telegram_token"] = token
    save_config(cfg)
    _USERNAMES.pop(token, None)
    return ok({"username": _bot_username(token) if token else None})


def bot_start(request: Request):
    if background.is_running():
        return ok({"running": True})
    background.start()
    return ok({"starting": True})


def bot_stop(request: Request):
    background.request_stop()
    return ok({"stopping": True})


async def bot_autostart(request: Request):
    body = await request.json()
    try:
        background.set_autostart(bool(body.get("enabled")))
    except OSError as e:
        return err(str(e))
    return ok({"autostart": background.autostart_enabled()})


def bot_unlink(request: Request):
    cfg = load_config()
    cfg["telegram_chat_id"] = ""
    cfg["codice_collegamento"] = ""
    save_config(cfg)
    return ok()


# -- azioni ------------------------------------------------------------------ #
def scan_now(request: Request):
    store.kv_set("force_scan", True)
    eng = engine.current()
    if eng is not None:  # il motore gira qui: scansione subito, senza aspettare il prossimo giro
        threading.Thread(target=eng.tick, daemon=True).start()
    return ok({"queued": True})


def notify_test(request: Request):
    cfg = load_config()
    from stocks.notify import notify_all

    res = notify_all(cfg, "🔔 Radar Web – prova", "Le notifiche funzionano.", "Le notifiche funzionano.")
    store.add_event("info", "🔔 Notifica di prova", "Se la vedi, le notifiche del browser funzionano.")
    return ok({"result": res})


def recap_send(request: Request):
    cfg = load_config()
    rec = reports.day_recap(_today())
    from stocks.notify import notify_all

    title = f"📋 Recap della giornata – {datetime.now():%d/%m}"
    res = notify_all(cfg, title, reports.recap_text(rec), f"{rec['massimi']} segnali, {rec['net_eur']:+.2f}€")
    store.add_event("recap", title, reports.recap_text(rec))
    return ok({"result": res})


# -- esportazioni ----------------------------------------------------------- #
EXPORT_COLS = ["id", "symbol", "created", "status", "score", "probability", "buy", "sell", "stop", "entry",
               "filled_at", "exit_price", "closed_at", "ret_pct", "net_pct", "hours", "mfe_pct", "mae_pct"]


def export(request: Request):
    name = request.path_params["name"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    if name in ("operazioni", "test"):
        rows = reports.operations("massimi" if name == "operazioni" else "test")
        w.writerow(EXPORT_COLS)
        for o in rows:
            w.writerow([_csv(o.get(c)) for c in EXPORT_COLS])
    elif name == "segnali":
        cols = ["id", "created", "kind", "status", "symbol", "timeframe", "score", "prev_score", "probability",
                "price", "buy", "sell", "stop"]
        w.writerow(cols)
        for s in store.signals(limit=100000):
            w.writerow([_csv(s.get(c)) for c in cols])
    elif name == "errori":
        cols = ["ts", "category", "source", "symbol", "message"]
        w.writerow(cols)
        for e in store.errors(limit=100000):
            w.writerow([_csv(e.get(c)) for c in cols])
    else:
        return err("esportazione sconosciuta", 404)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="radar_{name}.csv"'})


def _csv(v):
    if isinstance(v, float):
        return "" if not math.isfinite(v) else f"{v:.8g}".replace(".", ",")
    return "" if v is None else v


# --------------------------------------------------------------------------- #
# Applicazione
# --------------------------------------------------------------------------- #

class Guard:
    """Middleware ASGI: Host locale obbligatorio; POST solo con l'intestazione X-Radar (anti CSRF)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            host = headers.get("host", "").rsplit(":", 1)[0].strip("[]")
            bad = None
            if host not in ALLOWED_HOSTS:
                bad = "host non consentito"
            elif scope["method"] not in ("GET", "HEAD") and headers.get("x-radar") != "1":
                bad = "richiesta non consentita"
            if bad:
                resp = JSONResponse({"error": bad}, status_code=403)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app() -> Starlette:
    routes = [
        Route("/", index),
        Route("/api/overview", overview),
        Route("/api/radar", radar),
        Route("/api/signals", signals),
        Route("/api/operations", operations),
        Route("/api/stats", stats),
        Route("/api/recap", recap),
        Route("/api/recap/send", recap_send, methods=["POST"]),
        Route("/api/calendar", calendar),
        Route("/api/day", day),
        Route("/api/events", events),
        Route("/api/errors", errors_list),
        Route("/api/coin", coin),
        Route("/api/settings", settings_get, methods=["GET"]),
        Route("/api/settings", settings_post, methods=["POST"]),
        Route("/api/bot", bot_status),
        Route("/api/bot/token", bot_token, methods=["POST"]),
        Route("/api/bot/start", bot_start, methods=["POST"]),
        Route("/api/bot/stop", bot_stop, methods=["POST"]),
        Route("/api/bot/autostart", bot_autostart, methods=["POST"]),
        Route("/api/bot/unlink", bot_unlink, methods=["POST"]),
        Route("/api/scan", scan_now, methods=["POST"]),
        Route("/api/auto", auto_get),
        Route("/api/auto/settings", auto_settings, methods=["POST"]),
        Route("/api/auto/active", auto_active, methods=["POST"]),
        Route("/api/auto/mode", auto_mode, methods=["POST"]),
        Route("/api/auto/stop", auto_stop, methods=["POST"]),
        Route("/api/auto/deposit", auto_deposit, methods=["POST"]),
        Route("/api/auto/keys", auto_keys, methods=["POST"]),
        Route("/api/notify-test", notify_test, methods=["POST"]),
        Route("/api/lab", lab_get),
        Route("/api/market", market_get),
        Route("/api/lab/run", lab_run, methods=["POST"]),
        Route("/api/lab/auto", lab_auto, methods=["POST"]),
        Route("/api/pac", pac_get),
        Route("/api/pac/plan", pac_plan, methods=["POST"]),
        Route("/api/pac/buy", pac_buy, methods=["POST"]),
        Route("/api/pac/buy/delete", pac_buy_delete, methods=["POST"]),
        Route("/api/export/{name}.csv", export),
        Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(Guard)
    return app


def forward_test_here() -> bool:
    """Il test delle previsioni lo fa già il bot ogni ora: dal server solo se il bot è spento."""
    return bool(load_config().get("test_previsioni")) and not background.is_running()


def run(port: int | None = None, open_browser: bool = True, start_engine: bool = True) -> None:
    import uvicorn

    cfg = load_config()
    port = port or int(cfg.get("web_porta") or 8765)
    if start_engine:
        eng = engine.start_background("web", forward_test=forward_test_here)
        log.info("Motore segnali: %s", "avviato qui" if eng else "già attivo in un altro processo (bot)")
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Radar Web in esecuzione su {url}  (CTRL+C per chiudere)")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
