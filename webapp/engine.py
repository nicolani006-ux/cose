"""Motore dei segnali BUY massimo con preavviso.

Funzionamento (timeframe di default 1h, preavviso 20 minuti):

    hh:00 ─────────────── hh:40 ───────── hh:59 │ (hh+1):00
          candela in corso   ⏰ PREAVVISO         │ 🟢 CONFERMA alla chiusura

1. **Preavviso** (``preavviso_minuti`` prima della chiusura): il punteggio viene calcolato anche sulla
   candela ancora aperta. Le coin il cui punteggio *provvisorio* supera ``soglia_preavviso`` (e che alla
   candela precedente non erano già al massimo) ricevono un preavviso: "tra ~20 minuti potrebbe scattare".
2. **Conferma** (appena la candela si chiude): stessa analisi della scansione dell'app, sulle sole candele
   chiuse. Le coin con punteggio ≥ ``soglia_massimo`` e probabilità ≥ ``probabilita_minima`` danno il
   **segnale BUY massimo** con prezzi BUY/SELL/STOP. Ogni preavviso viene segnato confermato o annullato
   (un preavviso annullato è un errore di previsione e finisce nel calendario).
3. Ogni segnale massimo diventa un'operazione simulata seguita sui prezzi reali con le stesse regole del
   test delle previsioni (BUY limite valido 5 candele → SELL / STOP / scadenza). Le operazioni chiuse allo
   STOP sono errori di previsione.
4. Una volta al giorno (``riepilogo_ora``) arriva il recap della giornata.

Gira in un solo processo alla volta (porta locale ``ENGINE_LOCK_PORT``): nel server web o nel bot.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from indicators import score_series, sizing_from_config, sizing_text
from stocks.config import load_config
from webapp import store

log = logging.getLogger("radar.web.engine")

ENGINE_LOCK_PORT = 47616
TICK_SECONDS = 15
SCAN_RETRY_MS = 60_000
CONFIRM_DELAY_MS = 20_000        # attesa dopo la chiusura: l'exchange pubblica la candela con qualche secondo di ritardo
MIN_PREALERT_MS = 60_000         # a meno di 1 minuto dalla chiusura il preavviso non serve più
LOG_INGEST_EVERY = 300
RADAR_COLUMNS = ["symbol", "base", "price", "change_24h", "volume_24h", "score", "signal", "rsi", "vol_ratio",
                 "volatility", "trend_ema", "alerts", "buy", "sell", "stop", "upside_pct", "risk_pct", "probability",
                 "bt_trades", "bt_win_rate", "bt_avg_ret", "mc_win", "mc_verdict", "spark"]


def timeframe_ms(tf: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return int(tf[:-1] or 1) * units[tf[-1]]


def local_hm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def fmt_price(v) -> str:
    v = _num(v)
    if v is None:
        return "n/d"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.8f}".rstrip("0")


# --------------------------------------------------------------------------- #
# Dati di mercato (sostituibili nei test)
# --------------------------------------------------------------------------- #

def default_scan(cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Scansione sulle candele chiuse, con il punteggio della candela precedente (serve per il 'passaggio')."""
    import crypto_core

    ex, tf = cfg["segnali_exchange"], cfg["segnali_timeframe"]
    radar, errors = crypto_core.scan(ex, cfg["segnali_quote"], int(cfg["segnali_coins"]), tf,
                                     min_volume=float(cfg.get("segnali_volume_min") or 0))
    if radar.empty:
        return radar, errors
    prev = []
    for sym, score in zip(radar["symbol"], radar["score"]):
        if score < float(cfg["soglia_preavviso"]) - 15:  # lontane dalla soglia: non serve
            prev.append(np.nan)
            continue
        try:
            sc = score_series(crypto_core.fetch_ohlcv(ex, sym, tf))
            prev.append(float(sc.iloc[-2]) if len(sc) > 1 else np.nan)
        except Exception:  # noqa: BLE001
            prev.append(np.nan)
    radar["prev_score"] = prev
    if is_rimbalzo(cfg):
        add_rimb_fields(cfg, radar, lambda sym: crypto_core.fetch_ohlcv(ex, sym, tf))
    elif is_dip(cfg):
        add_dip_fields(cfg, radar, lambda sym: crypto_core.fetch_ohlcv(ex, sym, tf))
    return radar, errors


def is_dip(cfg: dict) -> bool:
    """Strategie con piano pronto (BUY subito, SELL e STOP fissi): 'dip' e 'rimbalzo' (stessi campi dip_*)."""
    return cfg.get("strategia") in ("dip", "rimbalzo")


def is_rimbalzo(cfg: dict) -> bool:
    return cfg.get("strategia") == "rimbalzo"


def add_rimb_fields(cfg: dict, radar: pd.DataFrame, fetch) -> None:
    """Segnale e piano della strategia 'Rimbalzo' per ogni coin del radar (serve anche BTC per il filtro)."""
    from webapp import lab_rimbalzo

    try:
        regime = lab_rimbalzo.btc_regime(fetch(f"BTC/{cfg['segnali_quote']}"))
    except Exception as e:  # noqa: BLE001 - senza BTC nessun segnale (prudente), ma la scansione continua
        store.add_error("tecnico", "scansione", f"Rimbalzo: candele BTC non scaricate: {type(e).__name__}: {e}")
        regime = pd.Series(dtype=bool)
    band, ks = float(cfg.get("rimb_banda") or 3.5), float(cfg.get("rimb_stop_atr") or 2)
    rows = []
    for sym in radar["symbol"]:
        try:
            rows.append(lab_rimbalzo.plan(fetch(sym), regime, band, ks))
        except Exception:  # noqa: BLE001 - una coin non deve fermare la scansione
            rows.append({"dip_hit": False})
    extra = pd.DataFrame(rows, index=radar.index)
    for col in extra.columns:
        radar[col] = extra[col]
    if "dip_hit" not in radar:
        radar["dip_hit"] = False
    radar["dip_hit"] = radar["dip_hit"].fillna(False).astype(bool)


def add_dip_fields(cfg: dict, radar: pd.DataFrame, fetch) -> None:
    """Segnale e piano della strategia 'Compra il ribasso' per ogni coin del radar (candele già in cache)."""
    from indicators import dip_plan

    rows = []
    for sym in radar["symbol"]:
        try:
            rows.append(dip_plan(fetch(sym), level=float(cfg.get("dip_rsi") or 35),
                                 k_stop=float(cfg.get("dip_stop_atr") or 2), k_target=float(cfg.get("dip_target_atr") or 3)))
        except Exception:  # noqa: BLE001 - una coin non deve fermare la scansione
            rows.append({"dip_hit": False})
    extra = pd.DataFrame(rows, index=radar.index)
    for col in extra.columns:
        radar[col] = extra[col]
    if "dip_hit" not in radar:
        radar["dip_hit"] = False
    radar["dip_hit"] = radar["dip_hit"].fillna(False).astype(bool)


def default_provisional_scan(cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    """Come la scansione, ma includendo la candela ancora aperta: punteggio *provvisorio*."""
    import crypto_core

    ex, tf = cfg["segnali_exchange"], cfg["segnali_timeframe"]
    tops = crypto_core.top_symbols(ex, cfg["segnali_quote"], int(cfg["segnali_coins"]),
                                   min_volume=float(cfg.get("segnali_volume_min") or 0))
    if tops.empty:
        return tops, []

    def work(row):
        try:
            df = crypto_core.fetch_ohlcv(ex, row["symbol"], tf, closed_only=False)
            res = crypto_core.analyze_symbol(ex, row["symbol"], tf, df)
            if not res:
                return None, None
            sc = score_series(df)
            return {**row, **res, "prev_score": float(sc.iloc[-2]) if len(sc) > 1 else np.nan}, None
        except Exception as e:  # noqa: BLE001
            return None, f"{row['symbol']}: {e}"

    with ThreadPoolExecutor(max_workers=crypto_core.SCAN_WORKERS) as pool:
        outcomes = list(pool.map(work, tops.to_dict("records")))
    out = pd.DataFrame([r for r, _ in outcomes if r])
    if not out.empty:
        out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out, [e for _, e in outcomes if e]


def default_fetch(exchange: str, symbol: str, timeframe: str, since_ms: int) -> list:
    from previsioni import _default_fetch

    return _default_fetch(exchange, symbol, timeframe, since_ms)


# --------------------------------------------------------------------------- #
# Motore
# --------------------------------------------------------------------------- #

class SignalEngine:
    def __init__(self, scan=default_scan, provisional_scan=default_provisional_scan, fetch=default_fetch,
                 notify=None, forward_test=None, process: str = "web", broker_factory=None):
        self.scan = scan
        self.provisional_scan = provisional_scan
        self.fetch = fetch
        self._notify = notify
        self.forward_test = forward_test   # funzione -> bool: eseguire anche il test delle previsioni?
        self.process = process
        self._lock = threading.Lock()
        self._last_ingest = 0.0
        from webapp.autotrade import AutoTrader

        self.trader = AutoTrader(self.notify, **({"broker_factory": broker_factory} if broker_factory else {}))

    # -- notifiche -------------------------------------------------------------- #
    def notify(self, cfg: dict, level: str, title: str, text: str, short: str, symbol: str | None = None,
               push: bool = True) -> None:
        store.add_event(level, title, text, symbol)
        if not push:
            return
        try:
            if self._notify is not None:
                status = self._notify(cfg, title, text, short)
            else:
                from stocks.notify import notify_all

                status = notify_all(cfg, title, text, short)
        except Exception as e:  # noqa: BLE001
            status = [f"notifiche: errore ({e})"]
        for s in status or []:
            if "errore" in s.lower():
                store.add_error("tecnico", "notifiche", s)

    # -- passo del motore --------------------------------------------------------- #
    def tick(self, now: float | None = None) -> dict:
        """Un passo: conferma della candela appena chiusa, preavviso sulla candela in corso, recap, registri.
        Ritorna cosa ha fatto (usato dai test e dalla pagina di stato)."""
        if not self._lock.acquire(blocking=False):
            return {"skipped": True}
        try:
            return self._tick(time.time() if now is None else now)
        finally:
            self._lock.release()

    def _auto_tick(self, cfg: dict, now_ms: int, done: dict) -> None:
        try:  # investimento automatico: ordini in attesa, SELL/STOP, comandi dall'interfaccia, piano di versamento
            done["auto"] = self.trader.tick(cfg, now_ms)
        except Exception as e:  # noqa: BLE001
            store.add_error("tecnico", "investimento", f"{type(e).__name__}: {e}")

    def _tick(self, now: float) -> dict:
        cfg = load_config()
        done: dict = {}
        state = store.kv_get("engine", {}) or {}
        now_ms = int(now * 1000)
        self._auto_tick(cfg, now_ms, done)  # STOP e SELL prima della scansione (che può durare un minuto)
        try:  # promemoria del PAC: un avviso al mese, il giorno del versamento
            from webapp import pac

            if pac.reminder(self.notify, cfg):
                done["pac"] = "promemoria"
        except Exception as e:  # noqa: BLE001
            store.add_error("tecnico", "pac", f"{type(e).__name__}: {e}")
        # la scansione può fallire (rete, exchange): non deve mai bloccare il controllo di STOP e SELL qui sotto
        if cfg.get("segnali_attivi", True) and now_ms >= int(state.get("scan_retry_ms") or 0):
            try:
                tf = cfg["segnali_timeframe"]
                tf_ms = timeframe_ms(tf)
                open_ms = now_ms // tf_ms * tf_ms
                close_ms = open_ms + tf_ms
                lead_ms = min(int(float(cfg.get("preavviso_minuti", 20)) * 60_000), tf_ms // 2)
                key = f"{cfg['segnali_exchange']}|{tf}"
                force = bool(store.kv_get("force_scan", False))
                if force:
                    store.kv_set("force_scan", False)
                if force or (state.get("confirmed") != f"{key}|{open_ms}" and now_ms >= open_ms + CONFIRM_DELAY_MS):
                    done["confirm"] = self.confirm(cfg, open_ms)
                    state["confirmed"] = f"{key}|{open_ms}"
                    state["last_confirm"] = store.iso_utc()
                if (state.get("prealerted") != f"{key}|{close_ms}"
                        and close_ms - lead_ms <= now_ms < close_ms - MIN_PREALERT_MS):
                    done["prealert"] = self.prealert(cfg, close_ms, now_ms)
                    state["prealerted"] = f"{key}|{close_ms}"
                    state["last_prealert"] = store.iso_utc()
                state.update(next_close=close_ms, next_prealert=close_ms - lead_ms, lead_min=lead_ms / 60_000,
                             timeframe=tf, exchange=cfg["segnali_exchange"])
            except Exception as e:  # noqa: BLE001
                log.exception("Errore della scansione")
                state["scan_retry_ms"] = now_ms + SCAN_RETRY_MS  # riprova fra un minuto, intanto gli STOP girano
                hour = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H")
                store.add_error("tecnico", "motore", f"scansione non riuscita: {type(e).__name__}: {e}"[:400],
                                ref=f"scan|{hour}")
        if "confirm" in done:  # dopo una scansione lunga: di nuovo, col prezzo aggiornato
            self._auto_tick(cfg, now_ms, done)
        done["recap"] = self.maybe_recap(cfg, state, datetime.fromtimestamp(now))
        if now - self._last_ingest >= LOG_INGEST_EVERY:
            self._last_ingest = now
            done["ingested"] = ingest_logs()
        state["heartbeat"] = store.iso_utc()
        state["process"] = self.process
        state["pid"] = os.getpid()
        store.kv_set("engine", state)
        return done

    # -- 1. preavviso ------------------------------------------------------------- #
    def prealert(self, cfg: dict, close_ms: int, now_ms: int | None = None) -> list[dict]:
        ex, tf = cfg["segnali_exchange"], cfg["segnali_timeframe"]
        try:
            radar, errs = self.provisional_scan(cfg)
        except Exception as e:  # noqa: BLE001
            store.add_error("tecnico", "preavviso", f"Scansione provvisoria fallita: {type(e).__name__}: {e}")
            return []
        self._log_scan_errors(errs, "preavviso")
        if radar.empty:
            return []
        if is_dip(cfg):  # nessun preavviso: in dashboard le coin con RSI vicino alla soglia
            lvl = 35.0 if is_rimbalzo(cfg) else float(cfg.get("dip_rsi") or 35)
            near = radar[pd.to_numeric(radar.get("rsi"), errors="coerce") <= lvl + 8].sort_values("rsi")
            store.kv_set("watch", {"close": close_ms, "at": store.iso_utc(), "rows": records(near.head(15))})
            return []
        th_pre, th_max = float(cfg["soglia_preavviso"]), float(cfg["soglia_massimo"])
        p_min = float(cfg["probabilita_minima"])
        minutes = max(1, round((close_ms - (now_ms or int(time.time() * 1000))) / 60_000))
        # coin "in avvicinamento" (solo per la dashboard): vicine alla soglia e in salita
        watch = radar[(radar["score"] >= th_pre - 15) & (radar["score"] > radar["prev_score"].fillna(-100))]
        store.kv_set("watch", {"close": close_ms, "at": store.iso_utc(), "rows": records(watch.head(15))})
        already = {s["symbol"] for s in store.signals(kind="preavviso", candle_close=close_ms)}
        sent = []
        for r in radar.to_dict("records"):
            prob = _num(r.get("probability")) or 0
            prev = _num(r.get("prev_score"))
            if r["score"] < th_pre or prob < p_min or r["symbol"] in already:
                continue
            if prev is not None and prev >= th_max:  # era già al massimo: non è una novità
                continue
            if self._in_pause(cfg, r["symbol"]):
                continue
            sid = store.add_signal("preavviso", r["symbol"], ex, tf, close_ms, status="attesa",
                                   details=_details(r, minutes=minutes), **_values(r))
            title = f"⏰ PREAVVISO BUY – {r['symbol']}"
            text = (f"Tra ~{minutes} minuti (chiusura candela {tf} delle {local_hm(close_ms)}) potrebbe scattare "
                    f"il segnale BUY massimo su {r['symbol']}.\n"
                    f"Punteggio provvisorio {r['score']:.0f} (soglia {th_max:.0f}, candela prima {_fmt(prev)}) · "
                    f"probabilità SELL {prob:.0f}%\n"
                    f"Piano provvisorio: BUY {fmt_price(r.get('buy'))} · SELL {fmt_price(r.get('sell'))} · "
                    f"STOP {fmt_price(r.get('stop'))}\n"
                    "Aspetta la conferma alla chiusura della candela prima di comprare.")
            self.notify(cfg, "preavviso", title, text, f"{r['symbol']}: possibile BUY massimo tra ~{minutes} min",
                        r["symbol"], push=bool(cfg.get("notifica_preavviso", True)))
            sent.append({"id": sid, "symbol": r["symbol"], "score": r["score"]})
        return sent

    # -- 2. conferma alla chiusura ---------------------------------------------- #
    def confirm(self, cfg: dict, closed_ms: int) -> dict:
        ex, tf = cfg["segnali_exchange"], cfg["segnali_timeframe"]
        out: dict = {"signals": [], "confirmed": [], "cancelled": [], "closed_ops": []}
        try:
            radar, errs = self.scan(cfg)
        except Exception as e:  # noqa: BLE001
            store.add_error("tecnico", "scansione", f"Exchange {ex} non raggiungibile: {type(e).__name__}: {e}")
            store.add_event("errore", "⚠️ Scansione non riuscita", f"{type(e).__name__}: {e}"[:300])
            self.update_operations(cfg, out)
            return out
        self._log_scan_errors(errs, "scansione")
        store.kv_set("radar", {"at": store.iso_utc(), "candle_close": closed_ms, "exchange": ex, "timeframe": tf,
                               "rows": records(radar)})
        th_max, p_min = float(cfg["soglia_massimo"]), float(cfg["probabilita_minima"])
        if radar.empty:
            self.update_operations(cfg, out)
            return out
        prob = pd.to_numeric(radar.get("probability"), errors="coerce").fillna(0)
        if is_dip(cfg):
            hits = radar[radar["dip_hit"]].copy() if "dip_hit" in radar else radar.iloc[0:0]
            for a, b in (("buy", "dip_buy"), ("sell", "dip_sell"), ("stop", "dip_stop"), ("upside_pct", "dip_upside_pct"),
                         ("risk_pct", "dip_risk_pct")):
                if b in hits:
                    hits[a] = hits[b]
        else:
            hits = radar[(radar["score"] >= th_max) & (prob >= p_min)]
        hit_syms = set(hits["symbol"])

        # preavvisi di questa candela: confermati o annullati
        pre = {s["symbol"]: s for s in store.signals(kind="preavviso", candle_close=closed_ms) if s["status"] == "attesa"}
        for sym, s in pre.items():
            if sym in hit_syms:
                store.set_signal_status(s["id"], "confermato")
                out["confirmed"].append(sym)
            else:
                store.set_signal_status(s["id"], "annullato")
                out["cancelled"].append(sym)
                final = radar.loc[radar["symbol"] == sym, "score"]
                fin = f"{float(final.iloc[0]):.0f}" if len(final) else "n/d"
                store.add_error("previsione", "preavviso", f"Preavviso non confermato: punteggio provvisorio "
                                f"{s['score']:.0f} → finale {fin} (soglia {th_max:.0f})", symbol=sym,
                                ref=f"preavviso|{s['id']}")
        if out["cancelled"]:
            store.add_event("info", "Preavvisi non confermati", ", ".join(out["cancelled"]))

        chosen = []
        for r in hits.to_dict("records"):
            prev = _num(r.get("prev_score"))
            announced = r["symbol"] in pre
            if not is_dip(cfg) and prev is not None and prev >= th_max and not announced:  # era già al massimo
                continue
            if self._in_pause(cfg, r["symbol"]):
                continue
            chosen.append({**r, "announced": announced})
            store.add_signal("massimo", r["symbol"], ex, tf, closed_ms, status="preavvisato" if announced else "",
                             details=_details(r, announced=announced), **_values(r))
            out["signals"].append(r["symbol"])
            self._notify_max(cfg, r, announced, closed_ms)

        if chosen:  # operazione simulata, seguita sui prezzi reali
            from previsioni import record_signals

            ops = store.load_operations()
            new = record_signals(ops, pd.DataFrame(chosen), ex, tf)
            store.save_operations(new)
            try:
                out["bought"] = [p["symbol"] for p in self.trader.on_signals(cfg, chosen, closed_ms)]
            except Exception as e:  # noqa: BLE001
                store.add_error("tecnico", "investimento", f"{type(e).__name__}: {e}")
        self.update_operations(cfg, out)
        if self.forward_test is not None and self.forward_test():
            try:
                from previsioni import run

                res = run(cfg)
                if res.get("error"):
                    store.add_error("tecnico", "test previsioni", res["error"])
            except Exception as e:  # noqa: BLE001
                store.add_error("tecnico", "test previsioni", f"{type(e).__name__}: {e}")
        return out

    def _notify_max(self, cfg: dict, r: dict, announced: bool, closed_ms: int) -> None:
        sz = sizing_from_config(pd.Series(r), cfg, fractional=True)
        prob = _num(r.get("probability")) or 0
        if is_rimbalzo(cfg):
            title = f"🟢 RIMBALZO – {r['symbol']}"
            text = (f"{r['symbol']} · candela {cfg['segnali_timeframe']} chiusa alle {local_hm(closed_ms)}\n"
                    f"Prezzo crollato sotto la banda di Bollinger ({float(cfg.get('rimb_banda') or 3.5):g} deviazioni) "
                    f"con RSI {_num(r.get('rsi')) or 0:.0f} e BTC in salita · prezzo {fmt_price(r.get('price'))}\n"
                    f"🟢 BUY subito fino a {fmt_price(r.get('buy'))}\n"
                    f"🎯 SELL alla media {fmt_price(r.get('sell'))} (+{_num(r.get('upside_pct')) or 0:.1f}%)\n"
                    f"🛑 STOP {fmt_price(r.get('stop'))} (-{_num(r.get('risk_pct')) or 0:.1f}%)\n"
                    "ℹ️ Indicazione automatica, non consulenza finanziaria.")
            self.notify(cfg, "massimo", title, text, f"{r['symbol']}: rimbalzo a {fmt_price(r.get('price'))}",
                        r["symbol"])
            return
        if is_dip(cfg):
            title = f"🟢 COMPRA IL RIBASSO – {r['symbol']}"
            text = (f"{r['symbol']} · candela {cfg['segnali_timeframe']} chiusa alle {local_hm(closed_ms)}\n"
                    f"RSI sceso a {_num(r.get('rsi')) or 0:.0f} (sotto {float(cfg.get('dip_rsi') or 35):.0f}) con il prezzo "
                    f"sopra la media a 200 candele · prezzo {fmt_price(r.get('price'))}\n"
                    f"🟢 BUY subito fino a {fmt_price(r.get('buy'))}\n"
                    f"🎯 SELL {fmt_price(r.get('sell'))} (+{_num(r.get('upside_pct')) or 0:.1f}%)\n"
                    f"🛑 STOP {fmt_price(r.get('stop'))} (-{_num(r.get('risk_pct')) or 0:.1f}%)\n"
                    "ℹ️ Indicazione automatica, non consulenza finanziaria.")
            self.notify(cfg, "massimo", title, text, f"{r['symbol']}: compra il ribasso a {fmt_price(r.get('price'))}",
                        r["symbol"])
            return
        title = f"🟢 SEGNALE BUY MASSIMO – {r['symbol']}"
        text = (f"{r['symbol']} · candela {cfg['segnali_timeframe']} chiusa alle {local_hm(closed_ms)}"
                f"{' · ✅ preavviso confermato' if announced else ''}\n"
                f"Punteggio {r['score']:.0f}/100 · probabilità SELL {prob:.0f}% · prezzo {fmt_price(r.get('price'))}\n"
                f"🟢 BUY {fmt_price(r.get('buy'))} (ordine limite, valido 5 candele)\n"
                f"🎯 SELL {fmt_price(r.get('sell'))} (+{_num(r.get('upside_pct')) or 0:.1f}%)\n"
                f"🛑 STOP {fmt_price(r.get('stop'))} (-{_num(r.get('risk_pct')) or 0:.1f}%)\n"
                + (f"Motivi: {r['alerts']}\n" if r.get("alerts") else "")
                + (sizing_text(sz, unit=" USDT") + "\n" if sz else "")
                + "ℹ️ Indicazione automatica, non consulenza finanziaria.")
        self.notify(cfg, "massimo", title, text, f"{r['symbol']}: BUY {fmt_price(r.get('buy'))}, "
                    f"probabilità {prob:.0f}%", r["symbol"])

    def update_operations(self, cfg: dict, out: dict) -> None:
        """Segue sui prezzi reali le operazioni dei segnali massimo e avvisa quando si chiudono."""
        from previsioni import STAKE_EUR, STATUS_LABEL, update_entries

        ops = store.load_operations()
        if not ops:
            return
        try:
            closed = update_entries(ops, self.fetch)
        except Exception as e:  # noqa: BLE001
            store.add_error("tecnico", "operazioni", f"Aggiornamento operazioni fallito: {e}")
            return
        store.save_operations(ops)
        for e in ops:
            if e.get("last_error"):
                store.add_error("tecnico", "operazioni", f"{e['symbol']}: {e['last_error']}", symbol=e["symbol"],
                                ref=f"op|{e['id']}|{e['last_error'][:60]}|{datetime.now():%Y-%m-%d %H}")
        for e in closed:
            out["closed_ops"].append(e["symbol"])
            eur = e["net_pct"] * STAKE_EUR / 100
            label = STATUS_LABEL[e["status"]]
            if e["status"] == "loss":
                store.add_error("previsione", "operazione", f"Segnale massimo finito allo STOP: {e['net_pct']:+.2f}% "
                                f"netto (probabilità {e['probability'] * 100:.0f}%)", symbol=e["symbol"],
                                ref=f"loss|{e['id']}", when=_parse_iso(e.get("closed_at")))
            self.notify(cfg, "info", f"{label.split(' ')[0]} {e['symbol']}: {label[2:]}",
                        f"{e['symbol']}: {label[2:]} · {e['ret_pct']:+.2f}% (netto {e['net_pct']:+.2f}%, "
                        f"{eur:+.2f}€ su {STAKE_EUR}€) dopo {e.get('hours', 0):.0f} ore",
                        f"{e['symbol']} {e['net_pct']:+.2f}%", e["symbol"])

    # -- 3. recap giornaliero ------------------------------------------------- #
    def maybe_recap(self, cfg: dict, state: dict, now: datetime) -> bool:
        if not cfg.get("riepilogo_giornaliero", True):
            return False
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(cfg.get("riepilogo_ora", "21:00")).strip())
        if not m:
            return False
        day = now.strftime("%Y-%m-%d")
        at = now.replace(hour=int(m.group(1)) % 24, minute=int(m.group(2)) % 60, second=0, microsecond=0)
        if state.get("recap_day") == day or now < at:
            return False
        state["recap_day"] = day
        from webapp.reports import day_recap, recap_text

        rec = day_recap(now.date())
        self.notify(cfg, "recap", f"📋 Recap della giornata – {now:%d/%m}", recap_text(rec),
                    f"{rec['massimi']} segnali, {rec['ops_closed']} operazioni chiuse, {rec['net_eur']:+.2f}€")
        return True

    # -- utilità ------------------------------------------------------------------ #
    def _in_pause(self, cfg: dict, symbol: str) -> bool:
        hours = float(cfg.get("pausa_stesso_simbolo_ore", 0) or 0)
        last = store.last_signal_time(symbol, "massimo")
        if not hours or not last:
            return False
        return datetime.fromisoformat(last) > store.now_utc() - timedelta(hours=hours)

    @staticmethod
    def _log_scan_errors(errs: list[str], source: str) -> None:
        hour = datetime.now().strftime("%Y-%m-%d %H")
        for e in errs or []:
            sym = e.split(":", 1)[0]
            store.add_error("tecnico", source, e, symbol=sym, ref=f"{source}|{sym}|{hour}")


def _fmt(v) -> str:
    return "n/d" if v is None else f"{v:.0f}"


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _values(r: dict) -> dict:
    return {k: _num(r.get(k)) for k in store.SIGNAL_COLS}


def _details(r: dict, **extra) -> dict:
    keys = ("alerts", "signal", "rsi", "vol_ratio", "bt_trades", "bt_win_rate", "bt_avg_ret", "mc_win", "mc_verdict",
            "change_24h")
    return {**{k: (_num(r.get(k)) if k not in ("alerts", "signal", "mc_verdict") else r.get(k)) for k in keys}, **extra}


def records(df: pd.DataFrame) -> list[dict]:
    """Righe del radar pronte per il JSON (NaN -> None)."""
    if df is None or df.empty:
        return []
    cols = [c for c in RADAR_COLUMNS + ["prev_score"] if c in df.columns]
    out = []
    for r in df[cols].to_dict("records"):
        row = {}
        for k, v in r.items():
            if k == "spark":
                row[k] = [x for x in (v or []) if _num(x) is not None][-50:] if isinstance(v, list) else []
            elif isinstance(v, str) or v is None:
                row[k] = v
            else:
                row[k] = _num(v)
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Errori dai registri del bot e del test previsioni -> calendario
# --------------------------------------------------------------------------- #

_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)? (ERROR|WARNING|ERRORE AVVIO)\s*(.*)$")
_TEST_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \| (.*ERRORE: .*)$")


def ingest_logs(max_age_days: int = 60) -> int:
    """Copia nel calendario gli errori registrati dal bot (bot.log) e dal test previsioni (registro_test.txt)."""
    from previsioni import RUN_LOG
    from stocks.background import LOG_FILE

    limit = datetime.now() - timedelta(days=max_age_days)
    added = 0
    for path, rx, fmt, source in ((LOG_FILE, _LOG_LINE, "%Y-%m-%d %H:%M:%S", "bot"),
                                  (RUN_LOG, _TEST_LINE, "%Y-%m-%d %H:%M", "test previsioni")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]
        except OSError:
            continue
        for line in lines:
            m = rx.match(line)
            if not m:
                continue
            when = datetime.strptime(m.group(1), fmt)
            if when < limit:
                continue
            msg = (m.group(3) if source == "bot" else m.group(2)).strip() or line
            if source == "bot" and m.group(2) == "WARNING" and not msg.startswith("Telegram"):
                continue
            ref = "log|" + hashlib.sha1(line.encode("utf-8", "replace")).hexdigest()
            added += store.add_error("tecnico", source, msg, ref=ref, when=when)
    return added


# --------------------------------------------------------------------------- #
# Avvio in background (un solo motore per PC)
# --------------------------------------------------------------------------- #

_LOCK_SOCKET: socket.socket | None = None
_ENGINE: SignalEngine | None = None


def engine_running_elsewhere() -> bool:
    if _ENGINE is not None:
        return False
    try:
        with socket.create_connection(("127.0.0.1", ENGINE_LOCK_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _try_lock() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", ENGINE_LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def start_background(process: str = "web", forward_test=None, stop: threading.Event | None = None,
                     takeover: bool = True) -> SignalEngine | None:
    """Avvia il motore in un thread se nessun altro processo lo sta già facendo. Ritorna il motore o None.
    Con `takeover`, se il motore gira altrove, ogni minuto controlla e lo prende in carico quando l'altro
    processo si chiude (es. si chiude l'app web ma il bot resta acceso)."""
    global _LOCK_SOCKET
    if _ENGINE is not None:
        return _ENGINE
    stop = stop or threading.Event()
    s = _try_lock()
    if s is None:
        if takeover:
            def wait_and_take():
                while not stop.wait(60):
                    lock = _try_lock()
                    if lock is not None:
                        _run(lock, process, forward_test, stop)
                        return
            threading.Thread(target=wait_and_take, name="motore-attesa", daemon=True).start()
        return None
    return _run(s, process, forward_test, stop)


def _run(lock: socket.socket, process: str, forward_test, stop: threading.Event) -> SignalEngine:
    global _LOCK_SOCKET, _ENGINE
    _LOCK_SOCKET = lock
    eng = SignalEngine(forward_test=forward_test, process=process)
    _ENGINE = eng

    def loop():
        log.info("Motore segnali avviato (%s)", process)
        while not stop.is_set():
            try:
                eng.tick()
                if eng.scan is default_scan and load_config().get("segnali_attivi", True):  # non nei test
                    from webapp import lab

                    lab.start()  # solo se i risultati hanno più di lab.REFRESH_HOURS ore
                    from webapp import market

                    market.refresh_async()  # sentimento e dati globali, ogni 30 minuti, in un thread a parte
            except Exception as e:  # noqa: BLE001 - il motore non deve mai fermarsi
                log.exception("Errore del motore segnali")
                try:
                    store.add_error("tecnico", "motore", f"{type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
            stop.wait(TICK_SECONDS)

    threading.Thread(target=loop, name="motore-segnali", daemon=True).start()
    return eng


def current() -> SignalEngine | None:
    return _ENGINE


def status() -> dict:
    """Stato del motore letto dal database (funziona anche se gira nel processo del bot)."""
    st = store.kv_get("engine", {}) or {}
    hb = _parse_iso(st.get("heartbeat"))
    alive = hb is not None and (store.now_utc() - hb).total_seconds() < TICK_SECONDS * 6
    return {**st, "alive": alive, "here": _ENGINE is not None}


def utc_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
