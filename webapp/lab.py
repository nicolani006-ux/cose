"""Laboratorio delle strategie: rigioca le regole del bot sui prezzi passati e sceglie la strategia migliore.

Metodo (vedi la skill backtesting-frameworks):
- nessun dato dal futuro: ogni decisione usa solo le candele chiuse fino a quel momento
  (punteggio, piano BUY/SELL/STOP e probabilità calcolati sulla storia tagliata a quella candela);
- costi reali: commissione dell'exchange per lato + slittamento sulle uscite allo STOP;
- regole identiche al bot: ordine BUY limite valido PLAN_FILL_BARS candele, poi SELL / STOP / scadenza,
  una sola operazione per coin, massimo operazioni aperte, pausa sulla stessa coin, importo per operazione;
- walk-forward: la strategia si sceglie sui primi 2/3 del periodo ("scelta") e si giudica sull'ultimo
  terzo ("verifica"), che non è servito a sceglierla. Si applica da sola solo se guadagna anche lì;
- pochi parametri (timeframe, soglia del punteggio, probabilità minima) per non adattarsi al rumore.

Limite noto: le coin sono le più scambiate di oggi (chi è sparito non c'è) e il passato non garantisce il futuro.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np

from webapp import store

log = logging.getLogger("radar.web.lab")

TIMEFRAMES = ("1h", "4h")
# strategia "dip" (compra il ribasso): soglia RSI e STOP/SELL in multipli di ATR
DIP_RSI_LEVELS = (30, 35)
DIP_STOPS = (1.5, 2.0, 3.0, 4.0)
DIP_TARGETS = (3.0, 4.0, 6.0, 8.0)
# durata massima dell'operazione in candele (poi si vende al mercato): con SELL lontani serve più tempo
DIP_HORIZONS = {"1h": (48, 120), "4h": (30, 60)}
MIN_PER_WEEK_CHOICE = 2.0      # la strategia scelta deve fare almeno 2 operazioni a settimana
DIP_HISTORY = {"1h": 8760, "4h": 2190}   # candele: 1 anno (più mercati diversi: rialzo, ribasso, laterale)
FETCH_WORKERS = 4                        # download in parallelo (l'exchange limita comunque la frequenza)
SCORE_THRESHOLDS = (40, 50, 60)
PROB_MINIMUMS = (35, 40, 45)
MIN_TRADES_CHOICE = 8          # operazioni minime nel periodo di scelta per prendere sul serio una strategia
MIN_TRADES_CHECK = 5           # e nel periodo di verifica per applicarla
STOP_SLIPPAGE_PCT = 0.1        # la vendita al mercato dopo lo STOP esce un po' sotto
START_CAPITAL = 100.0
REFRESH_HOURS = 6
CHOICE_SHARE = 2 / 3
KV_KEY = "lab"

_RUN_LOCK = threading.Lock()
_running = threading.Event()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="minutes")


# --------------------------------------------------------------------------- #
# 1. Candidati: ogni volta che il punteggio supera una delle soglie, il piano e come sarebbe finito
# --------------------------------------------------------------------------- #

def collect(exchange: str, quote: str, timeframe: str, n_coins: int, fee_pct: float,
            fetch=None, tops=None) -> list[dict]:
    import crypto_core
    from indicators import HORIZON_BARS, score_series, simulate_trade

    fetch = fetch or (lambda sym: crypto_core.fetch_ohlcv(exchange, sym, timeframe))
    symbols = tops if tops is not None else list(crypto_core.top_symbols(exchange, quote, n_coins)["symbol"])
    horizon = HORIZON_BARS.get(timeframe, 48)
    lo_th = min(SCORE_THRESHOLDS)
    out: list[dict] = []
    for sym in symbols:
        try:
            df = fetch(sym)
        except Exception as e:  # noqa: BLE001 - una coin non deve fermare il laboratorio
            log.info("laboratorio: %s non scaricata: %s", sym, e)
            continue
        if len(df) < 300:
            continue
        sc = score_series(df).to_numpy()
        o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        # millisecondi qualunque sia l'unità interna (ns, us, ms) della colonna
        times = np.array([ts.value // 1_000_000 for ts in df["time"]], dtype=np.int64)
        for i in range(260, len(df) - 1):
            v, pv = sc[i], sc[i - 1]
            if not (v >= lo_th and any(pv < th <= v for th in SCORE_THRESHOLDS)):
                continue
            res = crypto_core.analyze_symbol(exchange, sym, timeframe, df.iloc[: i + 1].reset_index(drop=True))
            if not res:
                continue
            buy, sell, stop = (float(res.get(k) or np.nan) for k in ("buy", "sell", "stop"))
            if not (np.isfinite(buy) and sell > buy > stop > 0):
                continue
            sim = simulate_trade(o, h, lo, c, i, buy, stop, sell, horizon)
            st = sim["status"]
            if st in ("pending", "open"):
                continue  # ancora in corso a fine dati: non si sa come finisce
            row = {"symbol": sym, "t": int(times[i]), "score": float(v), "prev": float(pv),
                   "prob": float(res.get("probability") or 0), "buy": buy, "sell": sell, "stop": stop,
                   "status": st}
            if st != "unfilled":
                entry, exit_px = sim["entry"], sim["exit_price"]
                if st == "loss":
                    exit_px *= 1 - STOP_SLIPPAGE_PCT / 100
                row.update(entry=entry, exit=exit_px, t_in=int(times[sim["fill_bar"]]),
                           t_out=int(times[sim["exit_bar"]]),
                           ret=(exit_px * (1 - fee_pct / 100)) / (entry * (1 + fee_pct / 100)) * 100 - 100)
            out.append(row)
    out.sort(key=lambda r: (r["t"], -r["score"]))
    return out


def _cache_file(exchange: str, symbol: str, timeframe: str):
    from stocks.config import DATA_DIR

    d = DATA_DIR / "candele"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{exchange}_{symbol.replace('/', '-').replace(':', '-')}_{timeframe}.pkl"


def history(exchange: str, symbol: str, timeframe: str, n: int):
    """Ultime `n` candele chiuse. Le candele restano salvate su disco: dopo il primo download (a blocchi
    di 1000, il massimo che l'exchange dà per volta) si scaricano solo quelle nuove."""
    import pickle

    import crypto_core

    ex = crypto_core.get_exchange(exchange)
    tfm = crypto_core.timeframe_ms(timeframe)
    now = int(time.time() * 1000)
    start = now - n * tfm
    f = _cache_file(exchange, symbol, timeframe)
    rows: dict = {}
    try:
        rows = pickle.loads(f.read_bytes())
    except (OSError, ValueError, EOFError, pickle.UnpicklingError):
        rows = {}
    young_cached = bool(rows.pop("_young", False)) if isinstance(rows, dict) else False
    rows = rows if isinstance(rows, dict) else {}
    # la cache serve solo se copre l'inizio della finestra (o la coin è più giovane della finestra)
    if rows and (min(rows) <= start + tfm or young_cached):
        since = max(rows)
    else:
        rows, since = {}, start
    first_call = not rows
    while since < now - tfm:
        part = crypto_core._retry(ex.fetch_ohlcv, symbol, timeframe=timeframe, since=since, limit=1000)
        if not part:
            break
        rows.update({int(r[0]): r for r in part})
        nxt = int(part[-1][0]) + tfm
        if nxt <= since:
            break
        since = nxt
    keep = {k: v for k, v in rows.items() if k >= start - tfm}
    young = bool((first_call or young_cached) and keep and min(keep) > start + 24 * tfm)  # quotata da poco
    try:
        f.write_bytes(pickle.dumps({**keep, **({"_young": True} if young else {})}))
    except OSError as e:
        log.info("laboratorio: cache %s non salvata: %s", f.name, e)
    return crypto_core.ohlcv_to_df(crypto_core.drop_open_candle([keep[k] for k in sorted(keep)], timeframe))


def collect_dip(exchange: str, quote: str, timeframe: str, n_coins: int, fee_pct: float,
                fetch=None, tops=None) -> dict:
    """Candidati della strategia dip per ogni combinazione (soglia RSI, STOP, SELL): id -> (tf, parametri, lista)."""
    import crypto_core
    from indicators import DIP_ENTRY_BUFFER, HORIZON_BARS, add_indicators, dip_series, simulate_trade

    fetch = fetch or (lambda sym: history(exchange, sym, timeframe, DIP_HISTORY.get(timeframe, 1000)))
    symbols = tops if tops is not None else list(crypto_core.top_symbols(exchange, quote, n_coins)["symbol"])
    horizons = DIP_HORIZONS.get(timeframe, (HORIZON_BARS.get(timeframe, 48),))
    grid = [(lv, ks, kt, hz) for lv in DIP_RSI_LEVELS for ks in DIP_STOPS for kt in DIP_TARGETS if kt > ks
            for hz in horizons]
    out: dict = {dip_id(timeframe, *g): (timeframe, g, []) for g in grid}
    def load(sym):
        try:
            return sym, fetch(sym)
        except Exception as e:  # noqa: BLE001
            log.info("laboratorio: %s non scaricata: %s", sym, e)
            return sym, None

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        loaded = list(pool.map(load, symbols))
    for sym, df in loaded:
        if df is None or len(df) < 300:
            continue
        d = add_indicators(df)
        o, h, lo, c, a = (d[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
        rsi = d["rsi"].to_numpy()
        times = np.array([ts.value // 1_000_000 for ts in df["time"]], dtype=np.int64)
        for lv in DIP_RSI_LEVELS:
            for i in dip_series(df, d, lv):
                if i >= len(df) - 1 or not (np.isfinite(a[i]) and a[i] > 0):
                    continue
                buy = c[i] * (1 + DIP_ENTRY_BUFFER / 100)
                for (lv2, ks, kt, hz) in grid:
                    if lv2 == lv:
                        stop, sell = c[i] - ks * a[i], c[i] + kt * a[i]
                        if not (sell > buy > stop > 0):
                            continue
                        sim = simulate_trade(o, h, lo, c, i, buy, stop, sell, hz)
                        st = sim["status"]
                        if st in ("pending", "open"):
                            continue
                        row = {"symbol": sym, "t": int(times[i]), "score": -float(rsi[i]), "prev": 0.0, "prob": 0.0,
                               "buy": buy, "sell": sell, "stop": stop, "status": st}
                        if st != "unfilled":
                            entry, exit_px = sim["entry"], sim["exit_price"]
                            if st == "loss":
                                exit_px *= 1 - STOP_SLIPPAGE_PCT / 100
                            row.update(entry=entry, exit=exit_px, t_in=int(times[sim["fill_bar"]]),
                                       t_out=int(times[sim["exit_bar"]]),
                                       ret=(exit_px * (1 - fee_pct / 100)) / (entry * (1 + fee_pct / 100)) * 100 - 100)
                        out[dip_id(timeframe, lv, ks, kt, hz)][2].append(row)
    for v in out.values():
        v[2].sort(key=lambda r: (r["t"], -r["score"]))
    return out


# --------------------------------------------------------------------------- #
# 2. Portafoglio: le regole del bot applicate in ordine di tempo, partendo da START_CAPITAL
# --------------------------------------------------------------------------- #

def portfolio(cands: list[dict], th: float, pmin: float, rules: dict, start: float = START_CAPITAL,
              t_from: int | None = None, t_to: int | None = None, pick=None) -> dict:
    """`pick(r)`: quali candidati sono segnali (di base: punteggio che supera `th` e probabilità ≥ `pmin`)."""
    from webapp.autotrade import trade_size

    pause_ms = float(rules.get("pausa_stesso_simbolo_ore") or 0) * 3_600_000
    max_open = int(rules.get("auto_max_posizioni") or 3)
    cash, open_pos, trades, curve = start, [], [], []
    last_sig: dict[str, int] = {}
    t0 = t1 = None

    def close_until(t):
        nonlocal cash
        for p in sorted([p for p in open_pos if p["t_out"] <= t], key=lambda p: p["t_out"]):
            open_pos.remove(p)
            cash += p["cost"] + p["pnl"]
            curve.append({"t": p["t_out"], "v": round(cash + sum(q["cost"] for q in open_pos), 4)})

    for r in cands:
        if t_from is not None and r["t"] < t_from or t_to is not None and r["t"] >= t_to:
            continue
        t0 = t0 if t0 is not None else r["t"]
        t1 = r["t"]
        if not (pick(r) if pick else (r["score"] >= th > r["prev"] and r["prob"] >= pmin)):
            continue
        close_until(r["t"])
        if pause_ms and r["t"] - last_sig.get(r["symbol"], -10**15) < pause_ms:
            continue
        last_sig[r["symbol"]] = r["t"]
        if r["status"] == "unfilled" or any(p["symbol"] == r["symbol"] for p in open_pos):
            continue
        if len(open_pos) >= max_open:
            continue
        cost, _ = trade_size(rules, cash)
        if cost <= 0 or cost < 5:
            continue
        pnl = cost * r["ret"] / 100
        cash -= cost
        p = {**r, "cost": cost, "pnl": pnl}
        open_pos.append(p)
        trades.append(p)
    close_until(float("inf"))
    return {"trades": trades, "curve": curve, "summary": summarize(trades, start, t0, t1)}


def summarize(trades: list[dict], start: float, t0: int | None, t1: int | None) -> dict:
    n = len(trades)
    days = max((t1 - t0) / 86_400_000, 1) if t0 is not None and t1 is not None else 1
    pnl = [t["pnl"] for t in trades]
    gains, losses = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
    eq, peak, mdd = start, start, 0.0
    for t in sorted(trades, key=lambda t: t["t_out"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100 if peak else 0)
    total = sum(pnl)
    return {"trades": n, "days": round(days, 1), "per_week": round(n / days * 7, 2),
            "wins": sum(1 for t in trades if t["status"] == "win"),
            "losses": sum(1 for t in trades if t["status"] == "loss"),
            "win_rate": round(sum(1 for x in pnl if x > 0) / n * 100, 1) if n else None,
            "pnl": round(total, 2), "return_pct": round(total / start * 100, 2),
            "avg_ret": round(float(np.mean([t["ret"] for t in trades])), 2) if n else None,
            "profit_factor": round(gains / losses, 2) if losses else (None if not gains else 99.0),
            "max_drawdown": round(mdd, 2)}


# --------------------------------------------------------------------------- #
# 3. Walk-forward: scelta sui primi 2/3, verifica sull'ultimo terzo
# --------------------------------------------------------------------------- #

def _wf_row(sid: str, cands: list[dict], rules: dict, pick=None, th=0.0, pm=0.0, **meta) -> dict | None:
    if not cands:
        return None
    t_start, t_end = cands[0]["t"], cands[-1]["t"]
    cut = int(t_start + (t_end - t_start) * CHOICE_SHARE)
    return {"id": sid, **meta, "cut": _iso(cut), "from": _iso(t_start), "to": _iso(t_end),
            "choice": portfolio(cands, th, pm, rules, t_to=cut, pick=pick)["summary"],
            "check": portfolio(cands, th, pm, rules, t_from=cut, pick=pick)["summary"],
            "full": portfolio(cands, th, pm, rules, pick=pick)["summary"]}


def dip_id(tf: str, rsi: float, ks: float, kt: float, hz: int | None = None) -> str:
    return f"dip|{tf}|{float(rsi):g}|{float(ks):g}|{float(kt):g}" + (f"|{int(hz)}" if hz else "")


def evaluate(cands_by_tf: dict[str, list[dict]], rules: dict, dip: dict | None = None,
             rimb: dict | None = None) -> dict:
    rows = []
    for sid, (tf, params, cands) in (dip or {}).items():
        lvl, ks, kt, hz = (tuple(params) + (None,))[:4]
        row = _wf_row(sid, cands, rules, pick=lambda r: True, kind="dip", timeframe=tf, rsi=lvl, stop_atr=ks,
                      target_atr=kt, horizon=hz)
        if row:
            rows.append(row)
    for sid, (tf, (band, ks, hz), cands) in (rimb or {}).items():  # solo osservazione: mai applicata dal Laboratorio
        row = _wf_row(sid, cands, rules, pick=lambda r: True, kind="rimbalzo", timeframe=tf, band=band, stop_atr=ks,
                      horizon=hz, research=True)
        if row:
            rows.append(row)
    for tf, cands in cands_by_tf.items():
        if not cands:
            continue
        t_start, t_end = cands[0]["t"], cands[-1]["t"]
        cut = int(t_start + (t_end - t_start) * CHOICE_SHARE)
        for th in SCORE_THRESHOLDS:
            for pm in PROB_MINIMUMS:
                choice = portfolio(cands, th, pm, rules, t_to=cut)["summary"]
                check = portfolio(cands, th, pm, rules, t_from=cut)["summary"]
                full = portfolio(cands, th, pm, rules)["summary"]
                rows.append({"id": f"{tf}|{th}|{pm}", "kind": "trend", "timeframe": tf, "soglia": th, "probabilita": pm,
                             "cut": _iso(cut), "from": _iso(t_start), "to": _iso(t_end),
                             "choice": choice, "check": check, "full": full})

    def enough(r):  # abbastanza operazioni nel periodo di scelta per giudicare (e per essere utile)
        s = r["choice"]
        return s["trades"] >= MIN_TRADES_CHOICE and (s.get("per_week") or 0) >= MIN_PER_WEEK_CHOICE

    def choice_rank(r):  # guadagno al giorno nel periodo di scelta
        s = r["choice"]
        return s["pnl"] / s["days"] if enough(r) else -1e9

    def passes(r):
        return bool(r and r["choice"]["pnl"] > 0 and r["check"]["trades"] >= MIN_TRADES_CHECK
                    and r["check"]["pnl"] > 0 and (r["check"]["profit_factor"] or 0) > 1)

    rows.sort(key=choice_rank, reverse=True)
    best = next((r for r in rows if enough(r) and not r.get("research")), None)
    watch = next((r for r in rows if enough(r) and r.get("research")), None)
    for r in rows:
        r["chosen"] = best is not None and r["id"] == best["id"]
    return {"rows": rows, "best": best["id"] if best else None, "verified": passes(best),
            "watch": {"id": watch["id"], "verified": passes(watch)} if watch else None}


# --------------------------------------------------------------------------- #
# 4. Esecuzione, salvataggio e applicazione automatica
# --------------------------------------------------------------------------- #

def current_id(cfg: dict) -> str:
    if cfg.get("strategia") == "rimbalzo":
        from webapp.lab_rimbalzo import rimb_id

        return rimb_id(cfg.get("segnali_timeframe"), cfg.get("rimb_banda") or 3.5, cfg.get("rimb_stop_atr") or 2,
                       cfg.get("rimb_candele_max") or 24)
    if cfg.get("strategia") == "dip":
        return dip_id(cfg.get("segnali_timeframe"), cfg.get("dip_rsi") or 35, cfg.get("dip_stop_atr") or 2,
                      cfg.get("dip_target_atr") or 3, cfg.get("dip_candele_max"))
    th = int(float(cfg.get("soglia_massimo") or 50))
    pm = int(float(cfg.get("probabilita_minima") or 40))
    return f"{cfg.get('segnali_timeframe')}|{th}|{pm}"


def run(cfg: dict | None = None, collect_fn=collect, collect_dip_fn=collect_dip, collect_rimb_fn=None) -> dict:
    from stocks.config import load_config, save_config
    from webapp import lab_rimbalzo
    from webapp.autotrade import fee_pct

    collect_rimb_fn = collect_rimb_fn or lab_rimbalzo.collect

    cfg = cfg or load_config()
    ex, quote, n = cfg["segnali_exchange"], cfg["segnali_quote"], int(cfg.get("segnali_coins") or 30)
    rules = {k: cfg.get(k) for k in ("auto_percentuale", "auto_importo_min", "auto_importo_max", "auto_riserva",
                                    "auto_max_posizioni", "pausa_stesso_simbolo_ore")}
    t0 = time.time()
    cands = {tf: collect_fn(ex, quote, tf, n, fee_pct(ex)) for tf in TIMEFRAMES}
    dip: dict = {}
    for tf in TIMEFRAMES:
        dip.update(collect_dip_fn(ex, quote, tf, n, fee_pct(ex)))
    rimb: dict = {}
    for tf in lab_rimbalzo.TIMEFRAMES:
        try:  # una strategia in osservazione non deve fermare il laboratorio
            rimb.update(collect_rimb_fn(ex, quote, tf, n, fee_pct(ex)))
        except Exception as e:  # noqa: BLE001
            log.warning("laboratorio: rimbalzo non calcolato: %s", e)
    ev = evaluate(cands, rules, dip, rimb)
    best = next((r for r in ev["rows"] if r["chosen"]), None)
    current = current_id(cfg)
    applied = None
    # con i soldi veri il Laboratorio non cambia mai la strategia da solo: la propone e decidi tu
    # né quando la strategia in uso è il rimbalzo, scelto a mano per provarlo in simulazione
    auto_ok = (cfg.get("lab_auto_applica", True) and cfg.get("auto_modalita") != "reale"
               and cfg.get("strategia") != "rimbalzo")
    if auto_ok and best and ev["verified"] and best["id"] != current:
        new = load_config()
        if best.get("kind") == "dip":
            new.update(strategia="dip", segnali_timeframe=best["timeframe"], dip_rsi=best["rsi"],
                       dip_stop_atr=best["stop_atr"], dip_target_atr=best["target_atr"])
            if best.get("horizon"):
                new["dip_candele_max"] = int(best["horizon"])
            what = (f"Compra il ribasso, candele {best['timeframe']}, RSI < {best['rsi']:g}, "
                    f"STOP {best['stop_atr']:g} ATR, SELL {best['target_atr']:g} ATR"
                    + (f", durata max {int(best['horizon'])} candele" if best.get("horizon") else ""))
        else:
            new.update(strategia="trend", segnali_timeframe=best["timeframe"], soglia_massimo=best["soglia"],
                       soglia_preavviso=max(best["soglia"] - 5, 10), probabilita_minima=best["probabilita"])
            what = f"Candele {best['timeframe']}, punteggio ≥ {best['soglia']}, probabilità ≥ {best['probabilita']}%"
        save_config(new)
        applied = best["id"]
        c = best["check"]
        store.add_event("auto", "🧪 Laboratorio: nuova strategia applicata",
                        f"{what}. Nel periodo di verifica: {c['trades']} operazioni, "
                        f"{c['pnl']:+.2f} {quote} su {START_CAPITAL:.0f} ({c['win_rate']}% vinte).")
        current = best["id"]
    keep = ("symbol", "t", "t_in", "t_out", "score", "prob", "entry", "exit", "status", "ret", "cost", "pnl")

    def detail_of(row):
        if row.get("kind") in ("dip", "rimbalzo"):
            pf = portfolio({**dip, **rimb}[row["id"]][2], 0, 0, rules, pick=lambda r: True)
        else:
            pf = portfolio(cands[row["timeframe"]], row["soglia"], row["probabilita"], rules)
        return {"id": row["id"], "trades": [{k: t.get(k) for k in keep} for t in pf["trades"]][-300:],
                "curve": pf["curve"], "summary": pf["summary"], "cut": row["cut"]}

    shown = next((r for r in ev["rows"] if r["id"] == current), None) or best
    # operazioni e capitale della strategia in uso (o della migliore se quella in uso non è nella griglia)
    detail = detail_of(shown) if shown else None
    if ev["watch"]:  # la migliore in osservazione, con la sua curva (per confrontarla con quella in uso)
        w = next(r for r in ev["rows"] if r["id"] == ev["watch"]["id"])
        ev["watch"].update(detail_of(w), check=w["check"], choice=w["choice"], full=w["full"])
    result = {"generated_at": store.iso_utc(), "seconds": round(time.time() - t0), "exchange": ex, "quote": quote,
              "coins": n, "fee_pct": fee_pct(ex), "start_capital": START_CAPITAL, "current": current,
              "applied": applied, "auto_apply": bool(cfg.get("lab_auto_applica", True)),
              "candidates": {**{tf: len(v) for tf, v in cands.items()},
                             **{k: len(v[2]) for k, v in {**dip, **rimb}.items()}}, **ev, "detail": detail}
    store.kv_set(KV_KEY, result)
    log.info("laboratorio: %d strategie provate in %ds, migliore %s (verificata: %s)", len(ev["rows"]),
             result["seconds"], ev["best"], ev["verified"])
    return result


def is_running() -> bool:
    return _running.is_set()


def start(force: bool = False) -> bool:
    """Avvia il laboratorio in un thread se non gira già e (salvo `force`) se i risultati sono vecchi."""
    if _running.is_set():
        return False
    if not force:
        last = (store.kv_get(KV_KEY) or {}).get("generated_at")
        if last:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if age < REFRESH_HOURS * 3600:
                return False
    if not _RUN_LOCK.acquire(blocking=False):
        return False
    _running.set()

    def work():
        try:
            run()
        except Exception as e:  # noqa: BLE001
            log.exception("laboratorio non riuscito")
            store.add_error("tecnico", "laboratorio", f"{type(e).__name__}: {e}")
        finally:
            _running.clear()
            _RUN_LOCK.release()

    threading.Thread(target=work, name="laboratorio", daemon=True).start()
    return True
