"""Indicatori tecnici e punteggio di trend condivisi da crypto e azioni."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI di Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # nessuna perdita nel periodo -> RSI 100
    out = out.where(~((loss == 0) & gain.notna()), 100.0)
    return out


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    line, sig, hist = macd(close)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    # tutte le colonne in un colpo solo: aggiungerle una per volta costa più del calcolo stesso
    cols = {
        "ema20": ema(close, 20), "ema50": ema(close, 50), "ema200": ema(close, 200), "rsi": rsi(close),
        "macd": line, "macd_signal": sig, "macd_hist": hist,
        "bb_mid": mid, "bb_up": mid + 2 * std, "bb_low": mid - 2 * std, "atr": atr(df),
    }
    return pd.concat([df.drop(columns=[c for c in cols if c in df.columns]), pd.DataFrame(cols, index=df.index)],
                     axis=1)


def _crossed(a: pd.Series, b: pd.Series, lookback: int) -> int:
    """+1 se `a` ha incrociato `b` verso l'alto nelle ultime `lookback` candele,
    -1 se verso il basso, 0 altrimenti."""
    diff = (a - b).dropna()
    if len(diff) < lookback + 1:
        return 0
    window = np.sign(diff.iloc[-(lookback + 1):].to_numpy())
    for prev, cur in zip(window[:-1], window[1:]):
        if prev <= 0 < cur:
            return 1
        if prev >= 0 > cur:
            return -1
    return 0


def _with_indicators(df: pd.DataFrame, d: pd.DataFrame | None) -> pd.DataFrame:
    """`d` = indicatori già calcolati per `df` (si evita di ricalcolarli più volte sulla stessa serie)."""
    return add_indicators(df) if d is None else d


def analyze(df: pd.DataFrame, d: pd.DataFrame | None = None) -> dict:
    """Calcola indicatori, punteggio di trend (-100..100) e segnali per una serie OHLCV."""
    df = _with_indicators(df, d)
    last = df.iloc[-1]
    close = float(last["close"])
    has_ema200 = len(df) >= 200

    score = 0.0
    # Trend (medie mobili)
    score += 15 if close > last["ema20"] else -15
    score += 15 if last["ema20"] > last["ema50"] else -15
    if has_ema200:
        score += 20 if last["ema50"] > last["ema200"] else -20
    # Momentum (MACD)
    score += 10 if last["macd_hist"] > 0 else -10
    macd_cross = _crossed(df["macd"], df["macd_signal"], 3)
    score += 10 * macd_cross
    # RSI
    r = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    if 55 <= r < 70:
        score += 15
    elif r >= 70:
        score += 5  # forte ma ipercomprato
    elif 30 < r <= 45:
        score -= 15
    elif r <= 30:
        score -= 5  # debole ma ipervenduto
    # Volume
    vol_avg = df["volume"].iloc[-21:-1].mean()
    vol_ratio = float(last["volume"] / vol_avg) if vol_avg and vol_avg > 0 else 1.0
    if vol_ratio >= 1.5:
        score *= 1.2
    # Breakout sui massimi/minimi delle ultime 20 candele
    hi20 = df["high"].iloc[-21:-1].max()
    lo20 = df["low"].iloc[-21:-1].min()
    breakout = 1 if close > hi20 else (-1 if close < lo20 else 0)
    score += 10 * breakout
    score = float(np.clip(score, -100, 100))

    alerts = []
    if r >= 70:
        alerts.append("RSI ipercomprato")
    elif r <= 30:
        alerts.append("RSI ipervenduto")
    if has_ema200:
        gc = _crossed(df["ema50"], df["ema200"], 5)
        if gc == 1:
            alerts.append("Golden cross")
        elif gc == -1:
            alerts.append("Death cross")
    if macd_cross == 1:
        alerts.append("MACD incrocio rialzista")
    elif macd_cross == -1:
        alerts.append("MACD incrocio ribassista")
    if vol_ratio >= 2:
        alerts.append(f"Volume x{vol_ratio:.1f}")
    if breakout == 1:
        alerts.append("Breakout massimi 20")
    elif breakout == -1:
        alerts.append("Rottura minimi 20")

    first = float(df["close"].iloc[0])
    return {
        "price": close,
        "rsi": r,
        "macd_hist": float(last["macd_hist"]),
        "trend_ema": "Rialzista" if last["ema20"] > last["ema50"] else "Ribassista",
        "vol_ratio": vol_ratio,
        "volatility": float(last["atr"] / close * 100) if pd.notna(last["atr"]) and close else np.nan,
        "change_period": (close / first - 1) * 100 if first else np.nan,
        "score": round(score, 1),
        "signal": signal_label(score),
        "alerts": ", ".join(alerts),
        "spark": df["close"].iloc[-50:].round(10).tolist(),
    }


def signal_label(score: float) -> str:
    if score >= 50:
        return "🟢 FORTE RIALZO"
    if score >= 20:
        return "🟢 Rialzo"
    if score <= -50:
        return "🔴 FORTE RIBASSO"
    if score <= -20:
        return "🔴 Ribasso"
    return "⚪ Neutrale"


# --------------------------------------------------------------------------- #
# Punteggio candela per candela, piano BUY / SELL / STOP e statistica storica
# --------------------------------------------------------------------------- #

def score_series(df: pd.DataFrame, d: pd.DataFrame | None = None) -> pd.Series:
    """Lo stesso punteggio di `analyze`, calcolato per ogni candela usando solo i dati
    disponibili fino a quella candela (serve per la statistica storica)."""
    d = _with_indicators(df, d).reset_index(drop=True)
    n = len(d)
    close = d["close"]
    s = np.where(close > d["ema20"], 15.0, -15.0)
    s += np.where(d["ema20"] > d["ema50"], 15.0, -15.0)
    s += np.where(np.arange(n) >= 199, np.where(d["ema50"] > d["ema200"], 20.0, -20.0), 0.0)
    s += np.where(d["macd_hist"] > 0, 10.0, -10.0)

    sign = np.sign((d["macd"] - d["macd_signal"]).to_numpy())
    cross = np.zeros(n)
    decided = np.zeros(n, dtype=bool)
    for k in (2, 1, 0):  # come _crossed: vince l'incrocio più vecchio nelle ultime 3 candele
        prev = np.roll(sign, k + 1)
        cur = np.roll(sign, k)
        up = (prev <= 0) & (cur > 0)
        dn = (prev >= 0) & (cur < 0)
        valid = np.arange(n) >= 3
        new_up = up & ~decided & valid
        new_dn = dn & ~decided & ~new_up & valid
        cross[new_up], cross[new_dn] = 1, -1
        decided |= new_up | new_dn
    s += 10 * cross

    r = d["rsi"].fillna(50).to_numpy()
    s += np.select([(r >= 55) & (r < 70), r >= 70, (r > 30) & (r <= 45), r <= 30], [15, 5, -15, -5], 0)

    vol_avg = d["volume"].rolling(20, min_periods=1).mean().shift(1)
    ratio = np.where(vol_avg > 0, d["volume"] / vol_avg, 1.0)
    s = np.where(ratio >= 1.5, s * 1.2, s)

    hi20 = d["high"].rolling(20, min_periods=1).max().shift(1)
    lo20 = d["low"].rolling(20, min_periods=1).min().shift(1)
    s += np.where(close > hi20, 10, np.where(close < lo20, -10, 0))
    return pd.Series(np.clip(s, -100, 100), index=df.index)


# Distanze in multipli di ATR (volatilità media) e durata massima di un'operazione, per timeframe.
PLAN_ENTRY_ATR = 0.5     # BUY: piccolo ritracciamento sotto il prezzo attuale
PLAN_STOP_ATR = 1.5      # STOP: 1,5 ATR sotto il BUY
PLAN_TARGET_ATR = 3.0    # SELL: 3 ATR sopra il BUY  (rendimento/rischio = 2)
PLAN_FILL_BARS = 5       # quante candele resta valido l'ordine di acquisto
HORIZON_BARS = {"15m": 96, "1h": 48, "4h": 30, "1d": 20}


def trade_plan(df: pd.DataFrame, d: pd.DataFrame | None = None) -> dict:
    """Prezzi suggeriti: BUY (acquisto con ordine limite), SELL (obiettivo), STOP (vendi se scende sotto)."""
    d = _with_indicators(df, d)
    last = d.iloc[-1]
    close, a = float(last["close"]), float(last["atr"]) if pd.notna(last["atr"]) else np.nan
    if not np.isfinite(a) or a <= 0:
        return {"buy": np.nan, "sell": np.nan, "stop": np.nan, "upside_pct": np.nan, "risk_pct": np.nan,
                "support": np.nan, "resistance": np.nan}
    buy = close - PLAN_ENTRY_ATR * a
    stop = buy - PLAN_STOP_ATR * a
    sell = buy + PLAN_TARGET_ATR * a
    recent = d.tail(60)
    return {
        "buy": buy, "sell": sell, "stop": max(stop, 0.0),
        "upside_pct": (sell / buy - 1) * 100, "risk_pct": (1 - stop / buy) * 100,
        "support": float(recent["low"].min()), "resistance": float(recent["high"].max()),
    }


def simulate_trade(o, h, lo, c, t: int, buy: float, stop: float, target: float, horizon: int,
                   fill_bars: int = PLAN_FILL_BARS) -> dict:
    """Simula il piano partendo dal segnale alla candela `t` (si usano le candele successive).

    status: "pending" (ordine BUY ancora valido e non eseguito), "unfilled" (mai eseguito),
    "open" (comprato, né SELL né STOP finora), "win" (SELL), "loss" (STOP), "timeout"
    (chiusa dopo `horizon` candele al prezzo di chiusura).
    Se nella stessa candela si toccano STOP e SELL, si considera persa (stima prudente)."""
    n = len(c)
    fill = next((j for j in range(t + 1, min(t + 1 + fill_bars, n)) if lo[j] <= buy), None)
    if fill is None:
        return {"status": "pending" if t + fill_bars > n - 1 else "unfilled"}
    entry = min(buy, o[fill])
    end = fill + horizon - 1
    out = {"fill_bar": fill, "entry": float(entry)}
    for k in range(fill, min(end, n - 1) + 1):
        if lo[k] <= stop:
            out.update(status="loss", exit_bar=k, exit_price=float(min(stop, o[k]) if k > fill else stop))
            break
        if k > fill and h[k] >= target:
            out.update(status="win", exit_bar=k, exit_price=float(max(target, o[k])))
            break
    else:
        if end >= n:
            out.update(status="open", exit_bar=n - 1, exit_price=float(c[n - 1]))
        else:
            out.update(status="timeout", exit_bar=end, exit_price=float(c[end]))
    last = out["exit_bar"]
    out["ret_pct"] = (out["exit_price"] / entry - 1) * 100
    out["mfe_pct"] = (max(h[fill + 1:last + 1], default=entry) / entry - 1) * 100  # massimo guadagno toccato
    out["mae_pct"] = (min(lo[fill:last + 1]) / entry - 1) * 100                   # massima perdita toccata
    return out


def backtest_plan(df: pd.DataFrame, horizon: int = 20, threshold: float = 20,
                  d: pd.DataFrame | None = None) -> dict:
    """Applica il piano a tutti i segnali d'acquisto passati della serie.

    Segnale = il punteggio supera `threshold`. Si piazza un ordine BUY (valido
    PLAN_FILL_BARS candele); se eseguito, l'operazione finisce al primo tra SELL
    (vinta), STOP (persa) o dopo `horizon` candele (vedi `simulate_trade`)."""
    d = _with_indicators(df, d).reset_index(drop=True)
    sc = score_series(df, d).to_numpy()
    o, h, lo, c, a = (d[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    n = len(d)
    wins = losses = timeouts = signals = 0
    returns, bars = [], []
    t, busy_until = 60, -1
    while t < n - 1:
        if not (sc[t] >= threshold and sc[t - 1] < threshold) or t <= busy_until or not np.isfinite(a[t]):
            t += 1
            continue
        signals += 1
        buy = c[t] - PLAN_ENTRY_ATR * a[t]
        stop, target = buy - PLAN_STOP_ATR * a[t], buy + PLAN_TARGET_ATR * a[t]
        res = simulate_trade(o, h, lo, c, t, buy, stop, target, horizon)
        if res["status"] == "unfilled":
            t += 1
            continue
        if res["status"] in ("pending", "open"):  # operazione ancora aperta a fine dati: non si conta
            break
        if res["status"] == "win":
            wins += 1
        elif res["status"] == "loss":
            losses += 1
        else:
            timeouts += 1
        returns.append(res["ret_pct"])
        bars.append(res["exit_bar"] - res["fill_bar"] + 1)
        busy_until = res["exit_bar"]
        t = res["exit_bar"] + 1
    trades = wins + losses + timeouts
    return {
        "bt_signals": signals, "bt_trades": trades, "bt_wins": wins, "bt_losses": losses, "bt_timeouts": timeouts,
        "bt_win_rate": wins / trades * 100 if trades else np.nan,
        "bt_fill_rate": trades / signals * 100 if signals else np.nan,
        "bt_avg_ret": float(np.mean(returns)) if returns else np.nan,
        "bt_avg_bars": float(np.mean(bars)) if bars else np.nan,
    }


def plan_and_stats(df: pd.DataFrame, horizon: int = 20, d: pd.DataFrame | None = None) -> dict:
    """Piano BUY/SELL/STOP + statistica storica + Time Machine (Monte Carlo) + probabilità combinata."""
    d = _with_indicators(df, d)
    plan = trade_plan(df, d)
    out = {**plan, **backtest_plan(df, horizon=horizon, d=d)}
    if np.isfinite(plan["buy"]):
        mc = monte_carlo_plan(df, plan["buy"], plan["stop"], plan["sell"], horizon)
        if mc.get("mc_replays"):
            out.update(mc)
    out["probability"] = combined_probability(out)
    return out


def combined_probability(row, global_rate: float = 1 / 3, prior_k: int = 5) -> float:
    """Probabilità (%) che il piano arrivi al SELL prima dello STOP una volta eseguito il BUY:
    media tra la storia reale del titolo (avvicinata a `global_rate` se i casi sono pochi)
    e la Time Machine. Se manca una delle due si usa l'altra."""
    trades = row.get("bt_trades") or 0
    wins = min(row.get("bt_wins") or 0, trades)
    hist = (wins + prior_k * global_rate) / (trades + prior_k) * 100
    mc = row.get("mc_win")
    if mc is None or pd.isna(mc):
        return float(hist)
    return float((hist + mc) / 2)


def stat_text(row, unit: str = "candele") -> str:
    """Frase breve sulla statistica storica di un titolo."""
    n = row.get("bt_trades", 0) or 0
    if not n:
        return "nessun segnale storico sufficiente"
    txt = (f"{row['bt_win_rate']:.0f}% dei {int(n)} acquisti passati ha raggiunto il SELL prima dello STOP "
           f"(rendimento medio {row['bt_avg_ret']:+.1f}% in {row['bt_avg_bars']:.0f} {unit})")
    fill = row.get("bt_fill_rate")
    if fill is not None and not pd.isna(fill) and fill < 100:
        txt += f"; l'ordine BUY è stato eseguito nel {fill:.0f}% dei segnali"
    if n < 5:
        txt += " ⚠️ campione piccolo, poco affidabile"
    return txt


def overall_stats(rows: pd.DataFrame) -> dict | None:
    """Statistica complessiva su tutti i titoli/coin: % di segnali arrivati al SELL e rendimento medio."""
    if rows.empty or "bt_trades" not in rows:
        return None
    trades = rows["bt_trades"].fillna(0)
    total = int(trades.sum())
    if not total:
        return None
    return {"trades": total, "win_rate": rows["bt_wins"].fillna(0).sum() / total * 100,
            "avg_ret": float((rows["bt_avg_ret"].fillna(0) * trades).sum() / total)}


PLAN_HELP = ("BUY = ordine d'acquisto (limite) mezzo ATR sotto il prezzo attuale, valido 5 candele · SELL = obiettivo 3 ATR sopra il BUY · "
             "STOP = vendi se scende 1,5 ATR sotto il BUY (rendimento/rischio 2:1). ATR = movimento medio di una candela. "
             "Probabilità SELL = media tra la storia reale (% dei segnali d'acquisto passati arrivati al SELL "
             "prima dello STOP) e la Time Machine (2000 simulazioni del futuro costruite con le candele passate "
             "della stessa ora del giorno).")


# --------------------------------------------------------------------------- #
# Time Machine: simulazione Monte Carlo del piano
# (idea e regole da "trader-timemachine" di day-trader-skill, J. Schincariol, licenza MIT;
#  qui riscritta in numpy e adattata al piano BUY limite / SELL / STOP / scadenza)
# --------------------------------------------------------------------------- #

MC_REPLAYS = 2000
MC_SEED = 19
MC_WINDOW = 2          # pesca candele di ±2 posizioni orarie attorno a quella da simulare
MC_COIN_FLIP = 5.0     # soglie del verdetto, in punti percentuali (come la Time Machine originale)
MC_STRONG = 15.0


def _slot_pool(d: pd.DataFrame):
    """Candele passate come rapporti sulla chiusura precedente, raggruppate per ora del giorno
    (per i dati giornalieri un solo gruppo). Ritorna (pool per slot, slot dell'ultima candela, n. slot)."""
    rel = np.column_stack([d["open"], d["high"], d["low"], d["close"]])[1:] / d["close"].to_numpy()[:-1, None]
    t = pd.to_datetime(d["time"], utc=True)
    step = (t.diff().dropna().median() if len(t) > 1 else pd.Timedelta(days=1))
    if step >= pd.Timedelta(hours=23):
        return [rel], 0, 1, step
    per_day = max(1, int(round(pd.Timedelta(days=1) / step)))
    minutes = (t.dt.hour * 60 + t.dt.minute).to_numpy()
    slots = (minutes // max(1, int(step / pd.Timedelta(minutes=1)))) % per_day
    pool = [rel[slots[1:] == s] for s in range(per_day)]
    return pool, int(slots[-1]), per_day, step


def monte_carlo_plan(df: pd.DataFrame, buy: float, stop: float, target: float, horizon: int,
                     replays: int = MC_REPLAYS, seed: int = MC_SEED, window: int = MC_WINDOW,
                     fill_bars: int = PLAN_FILL_BARS) -> dict:
    """Ricostruisce `replays` volte le candele future e applica il piano come `simulate_trade`.

    Ogni candela futura è una candela passata della stessa ora del giorno (± `window` slot),
    riscalata sul prezzo corrente. Stessa regola prudente: se una candela tocca STOP e SELL,
    vince lo STOP. Ritorna le probabilità (in %) e il verdetto."""
    d = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(d) < 60 or not (stop < buy < target):
        return {"mc_replays": 0}
    pool, last_slot, n_slots, _ = _slot_pool(d)
    rng = np.random.default_rng(seed)
    steps = fill_bars + horizon
    rel = np.empty((replays, steps, 4))
    for k in range(steps):
        base = (last_slot + 1 + k) % n_slots
        cand = [p for off in range(-window, window + 1) if len((p := pool[(base + off) % n_slots]))]
        cand = np.concatenate(cand) if cand else np.concatenate([p for p in pool if len(p)])
        rel[:, k, :] = cand[rng.integers(0, len(cand), replays)]
    closes = d["close"].iloc[-1] * np.cumprod(rel[:, :, 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), d["close"].iloc[-1]), closes[:, :-1]], axis=1)
    o, h, lo, c = (prev * rel[:, :, i] for i in range(4))

    idx = np.arange(steps)
    fill_ok = (lo[:, :fill_bars] <= buy)
    filled = fill_ok.any(axis=1)
    f = np.where(filled, fill_ok.argmax(axis=1), 0)
    entry = np.minimum(buy, o[np.arange(replays), f])
    end = f + horizon - 1
    in_trade = (idx[None, :] >= f[:, None]) & (idx[None, :] <= end[:, None])
    stop_hit = in_trade & (lo <= stop)
    tgt_hit = in_trade & (idx[None, :] > f[:, None]) & (h >= target)
    big = steps + 1
    k_stop = np.where(stop_hit.any(axis=1), stop_hit.argmax(axis=1), big)
    k_tgt = np.where(tgt_hit.any(axis=1), tgt_hit.argmax(axis=1), big)
    loss = filled & (k_stop < big) & (k_stop <= k_tgt)
    win = filled & (k_tgt < big) & (k_tgt < k_stop)
    timeout = filled & ~loss & ~win
    rows = np.arange(replays)
    exit_loss = np.where(k_stop == f, stop, np.minimum(stop, o[rows, np.minimum(k_stop, steps - 1)]))
    exit_win = np.maximum(target, o[rows, np.minimum(k_tgt, steps - 1)])
    exit_to = c[rows, np.minimum(end, steps - 1)]
    exit_price = np.where(loss, exit_loss, np.where(win, exit_win, exit_to))
    ret = np.where(filled, (exit_price / entry - 1) * 100, np.nan)

    n_f = int(filled.sum())
    p_win = win.sum() / n_f * 100 if n_f else np.nan
    p_loss = loss.sum() / n_f * 100 if n_f else np.nan
    r = (target - buy) / (buy - stop)
    hits = win.sum() + loss.sum()
    share = win.sum() / hits * 100 if hits else np.nan
    even = 100 / (1 + r)
    return {
        "mc_replays": replays, "mc_fill_rate": float(n_f / replays * 100),
        "mc_win": float(p_win), "mc_loss": float(p_loss),
        "mc_timeout": float(timeout.sum() / n_f * 100) if n_f else np.nan,
        "mc_avg_ret": float(np.nanmean(ret)) if n_f else np.nan,
        "mc_share": float(share), "mc_break_even": float(even), "mc_verdict": mc_verdict(share, even),
    }


def mc_verdict(share: float, even: float) -> str:
    """Verdetto in una frase: quota di SELL tra le simulazioni arrivate a SELL o STOP,
    confrontata con la quota che serve per andare in pari (1/(1+R): 33% con rendimento/rischio 2)."""
    if share is None or pd.isna(share):
        return "Nessuna simulazione ha toccato SELL o STOP: decide il prezzo a scadenza"
    gap = share - even
    if abs(gap) < MC_COIN_FLIP:
        head = "Vicino al pareggio"
    elif abs(gap) < MC_STRONG:
        head = "Tende verso il SELL" if gap > 0 else "Tende verso lo STOP"
    else:
        head = "Fortemente verso il SELL" if gap > 0 else "Fortemente verso lo STOP"
    return f"{head}: SELL nel {share:.0f}% dei casi decisi, ne serve il {even:.0f}% per andare in pari"


# --------------------------------------------------------------------------- #
# Quantità da comprare (regole di "trader-budget" di day-trader-skill, MIT)
# --------------------------------------------------------------------------- #

def position_size(buy: float, stop: float, target: float, capital: float, max_loss: float,
                  max_position_pct: float = 75, cash_buffer_pct: float = 10, max_order: float = 0,
                  fractional: bool = False) -> dict | None:
    """La quantità più piccola tra i limiti: perdita allo STOP ≤ `max_loss`, posizione ≤ `max_position_pct`
    del capitale, spesa ≤ capitale meno la riserva di liquidità, spesa ≤ `max_order` (0 = nessun limite).
    Azioni intere, oppure frazioni (crypto). None se il capitale non è impostato."""
    if not capital or capital <= 0 or not (0 < stop < buy) or not np.isfinite(buy):
        return None
    risk = buy - stop
    limits = {
        "perdita massima allo STOP": max_loss / risk if max_loss and max_loss > 0 else np.inf,
        "posizione massima": capital * max_position_pct / 100 / buy if max_position_pct else np.inf,
        "liquidità disponibile": capital * (1 - cash_buffer_pct / 100) / buy,
        "ordine massimo": max_order / buy if max_order and max_order > 0 else np.inf,
    }
    bound_by, q = min(limits.items(), key=lambda kv: kv[1])
    qty = np.floor(q * 1e6) / 1e6 if fractional else float(np.floor(q))
    out = {"qty": qty, "bound_by": bound_by, "fractional": fractional}
    if qty <= 0:
        out["why"] = (f"lo STOP è troppo lontano: anche 1 unità perderebbe {risk:,.2f}, più di {max_loss:,.2f}"
                      if bound_by.startswith("perdita") else f"limite raggiunto: {bound_by}")
        return out
    out.update(cost=qty * buy, loss_at_stop=qty * risk, gain_at_target=qty * (target - buy))
    return out


def sizing_from_config(row, cfg: dict, fractional: bool = False) -> dict | None:
    """position_size con le impostazioni dell'utente (capitale, rischio per operazione...)."""
    if row is None or pd.isna(row.get("buy")) or pd.isna(row.get("stop")):
        return None
    return position_size(row["buy"], row["stop"], row["sell"], float(cfg.get("capitale") or 0),
                         float(cfg.get("rischio_max_operazione") or 0), float(cfg.get("posizione_max_pct") or 0),
                         float(cfg.get("riserva_cassa_pct") or 0), float(cfg.get("ordine_max") or 0), fractional)


def sizing_text(sz: dict | None, unit: str = "$") -> str:
    if not sz:
        return ""
    if sz["qty"] <= 0:
        return f"💰 Non comprare: {sz['why']}"
    q = f"{sz['qty']:.6g}" if sz["fractional"] else f"{int(sz['qty'])}"
    return (f"💰 Quantità {q} (spesa {sz['cost']:,.2f}{unit}) · perdita allo STOP {sz['loss_at_stop']:,.2f}{unit} · "
            f"guadagno al SELL {sz['gain_at_target']:,.2f}{unit} · limite decisivo: {sz['bound_by']}")
