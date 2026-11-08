"""Test delle previsioni crypto sui prezzi reali (forward test) con report Excel.

Ad ogni controllo:
1. per ogni crypto con segnale d'acquisto viene registrata una previsione
   (probabilità, prezzo BUY, SELL, STOP, margine ipotizzato);
2. le previsioni aperte vengono seguite sulle candele reali successive con le stesse
   regole della statistica storica (BUY limite valido 5 candele, poi SELL / STOP / scadenza);
3. si aggiorna il file Excel con il guadagno effettivo e le statistiche.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from indicators import HORIZON_BARS, overall_stats, simulate_trade
from stocks.config import DATA_DIR, REPORT_DIR

JOURNAL_FILE = DATA_DIR / "previsioni.json"
EXCEL_FILE = REPORT_DIR / "test_previsioni.xlsx"
RUN_LOG = REPORT_DIR / "registro_test.txt"  # una riga per ogni aggiornamento (bot, app o excel_previsioni)
FEE_PCT = 0.1      # commissione per lato (Binance spot standard)
STAKE_EUR = 100    # importo simulato per ogni operazione
PRIOR_K = 5        # peso della media generale nella probabilità (pochi dati storici = stima prudente)
BUY_THRESHOLD = 20
MIN_MARGIN_PCT = 0.5  # sotto questo margine il guadagno sparirebbe nelle commissioni: non si registra

FINAL = {"unfilled", "win", "loss", "timeout"}
CLOSED = {"win", "loss", "timeout"}
STATUS_LABEL = {
    "pending": "⏳ In attesa del BUY", "unfilled": "❌ BUY non eseguito", "open": "🔄 Aperta",
    "win": "✅ SELL raggiunto", "loss": "🛑 STOP", "timeout": "⏱️ Scaduta",
}


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #

def load_journal() -> list[dict]:
    try:
        return json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_journal(journal: list[dict]) -> None:
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOURNAL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(JOURNAL_FILE)


def probability(wins, trades, global_rate: float) -> float:
    """Probabilità storica di arrivare al SELL prima dello STOP: successo passato della coin,
    avvicinato alla media generale quando i casi storici sono pochi. La probabilità registrata
    è la media tra questa e la Time Machine (simulazione Monte Carlo), se disponibile."""
    wins = 0 if wins is None or pd.isna(wins) else float(wins)
    trades = 0 if trades is None or pd.isna(trades) else float(trades)
    wins = min(max(wins, 0.0), trades)
    return float(np.clip((wins + PRIOR_K * global_rate) / (trades + PRIOR_K), 0, 1))


def record_signals(journal: list[dict], radar: pd.DataFrame, exchange: str, timeframe: str,
                   now: datetime | None = None) -> list[dict]:
    """Aggiunge una previsione per ogni crypto con segnale d'acquisto (una sola attiva per coin)."""
    if radar.empty:
        return []
    now = now or datetime.now(timezone.utc)
    active = {e["symbol"] for e in journal if e["status"] in ("pending", "open")}
    known = {e["id"] for e in journal}
    g = overall_stats(radar)
    g_rate = g["win_rate"] / 100 if g else 1 / 3
    new = []
    for r in radar[radar["score"] >= BUY_THRESHOLD].itertuples():
        if r.symbol in active or pd.isna(r.buy) or not r.buy > 0 or (r.sell / r.buy - 1) * 100 < MIN_MARGIN_PCT:
            continue
        eid = f"{exchange}|{r.symbol}|{timeframe}|{int(r.last_candle)}"
        if eid in known:
            continue
        hist = probability(r.bt_wins, r.bt_trades, g_rate)
        mc = getattr(r, "mc_win", None)
        mc = None if mc is None or pd.isna(mc) else float(mc) / 100
        e = {
            "id": eid, "symbol": r.symbol, "exchange": exchange, "timeframe": timeframe,
            "horizon": HORIZON_BARS.get(timeframe, 48), "created": now.isoformat(timespec="seconds"),
            "signal_ts": int(r.last_candle), "price": float(r.price), "score": float(r.score),
            "signal": r.signal, "buy": float(r.buy), "sell": float(r.sell), "stop": float(r.stop),
            "probability": round(hist if mc is None else (hist + mc) / 2, 4),
            "prob_hist": round(hist, 4), "prob_mc": None if mc is None else round(mc, 4),
            "mc_verdict": getattr(r, "mc_verdict", None) if mc is not None else None,
            "hist_trades": int(0 if pd.isna(r.bt_trades) else r.bt_trades),
            "expected_margin_pct": (r.sell / r.buy - 1) * 100,
            "status": "pending",
        }
        journal.append(e)
        new.append(e)
    return new


def _iso(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="minutes")


def update_entries(journal: list[dict], fetch_ohlcv) -> list[dict]:
    """Segue le previsioni non concluse sui prezzi reali. `fetch_ohlcv(exchange, symbol, timeframe, since_ms)`
    restituisce le candele [ts, open, high, low, close, volume] dalla candela del segnale in poi.
    Ritorna le operazioni appena chiuse."""
    closed_now = []
    for e in journal:
        if e["status"] in FINAL and e.get("chg_horizon_pct") is not None:
            continue
        try:
            rows = fetch_ohlcv(e["exchange"], e["symbol"], e["timeframe"], e["signal_ts"])
        except Exception as ex:  # noqa: BLE001 - riprova al prossimo controllo
            e["last_error"] = f"{type(ex).__name__}: {ex}"[:200]
            continue
        rows = [x for x in rows if x[0] >= e["signal_ts"]]
        if not rows:
            continue
        ts, o, h, lo, c = (np.array([x[i] for x in rows], dtype=float) for i in range(5))
        t = 0  # la prima candela è quella del segnale
        n = len(c)
        if t + e["horizon"] < n:  # direzione: il prezzo è salito dopo `horizon` candele?
            e["chg_horizon_pct"] = (c[t + e["horizon"]] / e["price"] - 1) * 100
        e["last_price"] = float(c[-1])
        e["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        e.pop("last_error", None)
        if e["status"] in FINAL:
            continue
        res = simulate_trade(o, h, lo, c, t, e["buy"], e["stop"], e["sell"], e["horizon"])
        e["status"] = res["status"]
        if "fill_bar" in res:
            f, x = res["fill_bar"], res["exit_bar"]
            e.update(entry=res["entry"], filled_at=_iso(ts[f]), mfe_pct=res["mfe_pct"], mae_pct=res["mae_pct"])
            if res["status"] in CLOSED:
                tf_ms = ts[1] - ts[0] if n > 1 else 0
                e.update(exit_price=res["exit_price"], closed_at=_iso(ts[x] + tf_ms), ret_pct=res["ret_pct"],
                         net_pct=(res["exit_price"] * (1 - FEE_PCT / 100) / (res["entry"] * (1 + FEE_PCT / 100)) - 1) * 100,
                         hours=(ts[x] + tf_ms - ts[f]) / 3_600_000)
                closed_now.append(e)
            else:
                e["unreal_pct"] = res["ret_pct"]
    return closed_now


# --------------------------------------------------------------------------- #
# Statistiche
# --------------------------------------------------------------------------- #

def to_frame(journal: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(journal)
    for col in ("ret_pct", "net_pct", "hours", "mfe_pct", "mae_pct", "chg_horizon_pct", "entry", "exit_price",
                "filled_at", "closed_at", "unreal_pct", "last_price"):
        if col not in df:
            df[col] = np.nan
    return df


def compute_stats(journal: list[dict]) -> dict:
    df = to_frame(journal)
    s: dict = {"total": len(df)}
    if df.empty:
        return s
    st = df["status"]
    closed = df[st.isin(CLOSED)].copy()
    executed = st.isin(CLOSED | {"open"}).sum()
    s.update(
        first=df["created"].min()[:10], last=df["created"].max()[:10],
        pending=int((st == "pending").sum()), open=int((st == "open").sum()), unfilled=int((st == "unfilled").sum()),
        closed=len(closed), wins=int((st == "win").sum()), losses=int((st == "loss").sum()),
        timeouts=int((st == "timeout").sum()),
        fill_rate=executed / (executed + (st == "unfilled").sum()) * 100 if executed + (st == "unfilled").sum() else np.nan,
    )
    if not closed.empty:
        win = (closed["status"] == "win").astype(float)
        closed = closed.sort_values("closed_at")
        eur = closed["net_pct"] * STAKE_EUR / 100
        curve = eur.cumsum()
        gains, losses_ = closed.loc[closed["net_pct"] > 0, "net_pct"].sum(), closed.loc[closed["net_pct"] < 0, "net_pct"].sum()
        s.update(
            win_rate=win.mean() * 100, prob_mean=closed["probability"].mean() * 100,
            brier=float(((closed["probability"] - win) ** 2).mean()),
            **_brier_parts(closed, win),
            margin_mean=closed["expected_margin_pct"].mean(), ret_mean=closed["ret_pct"].mean(),
            net_mean=closed["net_pct"].mean(), net_median=closed["net_pct"].median(),
            total_eur=float(eur.sum()), invested_eur=STAKE_EUR * len(closed),
            profit_factor=gains / -losses_ if losses_ < 0 else np.inf if gains > 0 else np.nan,
            best=closed.loc[closed["net_pct"].idxmax(), ["symbol", "net_pct"]].tolist(),
            worst=closed.loc[closed["net_pct"].idxmin(), ["symbol", "net_pct"]].tolist(),
            hours_mean=closed["hours"].mean(), mfe_mean=closed["mfe_pct"].mean(), mae_mean=closed["mae_pct"].mean(),
            max_drawdown_eur=float((curve - curve.cummax().clip(lower=0)).min()),
        )
    d = df["chg_horizon_pct"].dropna()
    if not d.empty:
        s.update(direction_n=len(d), direction_up=(d > 0).mean() * 100, direction_mean=d.mean())
    return s


def _brier_parts(closed: pd.DataFrame, win: pd.Series) -> dict:
    """Precisione separata delle due stime, sulle sole operazioni che le hanno entrambe."""
    out = {}
    for col, key in (("prob_hist", "brier_hist"), ("prob_mc", "brier_mc")):
        if col in closed:
            p = pd.to_numeric(closed[col], errors="coerce")
            ok = p.notna()
            if ok.any():
                out[key] = float(((p[ok] - win[ok]) ** 2).mean())
    return out


def summary_text(s: dict) -> str:
    if not s.get("total"):
        return "🧪 Test previsioni: nessuna previsione registrata finora."
    lines = [f"🧪 Test previsioni crypto (dal {s['first']}): {s['total']} previsioni",
             f"In attesa {s['pending']} · aperte {s['open']} · BUY non eseguito {s['unfilled']} · chiuse {s['closed']}"]
    if s.get("closed"):
        lines += [
            f"✅ SELL {s['wins']} · 🛑 STOP {s['losses']} · ⏱️ scadute {s['timeouts']}",
            f"Successo reale {s['win_rate']:.0f}% contro probabilità ipotizzata {s['prob_mean']:.0f}%",
            f"Margine ipotizzato medio +{s['margin_mean']:.2f}% · guadagno reale medio {s['ret_mean']:+.2f}% "
            f"(netto commissioni {s['net_mean']:+.2f}%)",
            f"Con {STAKE_EUR}€ per operazione: {s['total_eur']:+.2f}€ su {s['closed']} operazioni",
        ]
    if s.get("direction_n"):
        lines.append(f"Prezzo salito dopo l'orizzonte nel {s['direction_up']:.0f}% delle previsioni ({s['direction_n']})")
    return "\n".join(lines)


def closed_text(e: dict) -> str:
    return (f"{STATUS_LABEL[e['status']].split(' ')[0]} {e['symbol']}: {STATUS_LABEL[e['status']][2:]}, "
            f"{e['ret_pct']:+.2f}% (ipotizzato +{e['expected_margin_pct']:.2f}%, probabilità {e['probability'] * 100:.0f}%)")


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #

COLUMNS = [  # (intestazione, larghezza, formato)
    ("Crypto", 14, None),
    ("Probabilità di rialzo ipotizzata", 14, "0%"),
    ("Prezzo BUY → SELL ipotizzato", 30, None),
    ("Margine di guadagno ipotizzato", 14, "0.00%"),
    ("Guadagno effettivo", 13, "0.00%"),
    ("Esito", 20, None),
    ("Data previsione (UTC)", 19, None),
    ("Prezzo alla previsione", 14, "#,##0.########"),
    ("BUY", 13, "#,##0.########"),
    ("SELL", 13, "#,##0.########"),
    ("STOP", 13, "#,##0.########"),
    ("Punteggio", 10, "+0;-0"),
    ("Acquisto eseguito (UTC)", 19, None),
    ("Chiusura (UTC)", 19, None),
    ("Prezzo di uscita", 14, "#,##0.########"),
    ("Durata (ore)", 10, "0.0"),
    ("Guadagno netto commissioni", 13, "0.00%"),
    ("Massimo guadagno toccato", 13, "0.00%"),
    ("Massima perdita toccata", 13, "0.00%"),
    ("Variazione dopo l'orizzonte", 13, "0.00%"),
    ("Casi storici della coin", 10, "0"),
    ("Probabilità storica", 11, "0%"),
    ("Probabilità Time Machine", 11, "0%"),
    ("Verdetto Time Machine", 60, None),
    ("Exchange / timeframe", 16, None),
]


def _pct(v):
    return None if v is None or pd.isna(v) else v / 100


def _num(v):
    return None if v is None or pd.isna(v) else v


def write_excel(journal: list[dict], path=EXCEL_FILE) -> None:
    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)
    df = to_frame(journal)
    s = compute_stats(journal)
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    green, red = Font(color="1E8E3E", bold=True), Font(color="C62828", bold=True)

    def header(ws, names):
        ws.append(names)
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.freeze_panes = "B2"

    # -- Previsioni ---------------------------------------------------------- #
    ws = wb.active
    ws.title = "Previsioni"
    header(ws, [c[0] for c in COLUMNS])
    ws.row_dimensions[1].height = 45
    for e in df.sort_values("created", ascending=False).to_dict("records") if not df.empty else []:
        status = e["status"]
        esito = STATUS_LABEL[status]
        if status == "open" and not pd.isna(e.get("unreal_pct")):
            esito += f" (ora {e['unreal_pct']:+.2f}%)"
        ws.append([
            e["symbol"], e["probability"], f"BUY {e['buy']:.8g} → SELL {e['sell']:.8g}", _pct(e["expected_margin_pct"]),
            _pct(e["ret_pct"]) if status in CLOSED else None, esito, e["created"][:16].replace("T", " "),
            e["price"], e["buy"], e["sell"], e["stop"], e["score"],
            (e["filled_at"] or "")[:16].replace("T", " ") if isinstance(e["filled_at"], str) else None,
            (e["closed_at"] or "")[:16].replace("T", " ") if isinstance(e["closed_at"], str) else None,
            None if pd.isna(e["exit_price"]) else e["exit_price"], None if pd.isna(e["hours"]) else e["hours"],
            _pct(e["net_pct"]) if status in CLOSED else None, _pct(e["mfe_pct"]), _pct(e["mae_pct"]),
            _pct(e["chg_horizon_pct"]), e.get("hist_trades", 0),
            _num(e.get("prob_hist")), _num(e.get("prob_mc")), e.get("mc_verdict"),
            f"{e['exchange']} / {e['timeframe']}",
        ])
        row = ws.max_row
        for col in (5, 17):
            v = ws.cell(row, col).value
            if v is not None:
                ws.cell(row, col).font = green if v > 0 else red
    for i, (_, width, fmt) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
        if fmt:
            for (cell,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                cell.number_format = fmt
    ws.auto_filter.ref = ws.dimensions

    # -- Statistiche ---------------------------------------------------------- #
    st = wb.create_sheet("Statistiche")
    header(st, ["Statistica", "Valore", "Cosa significa"])

    def put(name, value, fmt=None, note=""):
        st.append([name, value, note])
        if fmt:
            st.cell(st.max_row, 2).number_format = fmt

    if s.get("total"):
        put("Periodo del test", f"{s['first']} → {s['last']}")
        put("Previsioni registrate", s["total"], "0", "Segnali d'acquisto registrati (una sola previsione attiva per coin)")
        put("In attesa del BUY", s["pending"], "0", "Ordine BUY ancora valido, prezzo non ancora sceso al BUY")
        put("Aperte", s["open"], "0", "Comprate, né SELL né STOP ancora toccati")
        put("BUY non eseguito", s["unfilled"], "0", "Il prezzo non è sceso al BUY entro 5 candele: nessuna operazione")
        put("% ordini BUY eseguiti", _pct(s["fill_rate"]), "0%")
        put("Operazioni chiuse", s["closed"], "0")
    if s.get("closed"):
        put("✅ Chiuse al SELL", s["wins"], "0")
        put("🛑 Chiuse allo STOP", s["losses"], "0")
        put("⏱️ Scadute", s["timeouts"], "0", "Né SELL né STOP entro l'orizzonte: chiuse al prezzo di quel momento")
        put("Successo reale (SELL prima dello STOP)", _pct(s["win_rate"]), "0.0%")
        put("Probabilità media ipotizzata", _pct(s["prob_mean"]), "0.0%",
            "Se è vicina al successo reale, le probabilità indicate sono affidabili")
        put("Brier score", s["brier"], "0.000", "Precisione delle probabilità: 0 = perfetta, 0,25 = come tirare a caso")
        if "brier_hist" in s:
            put("Brier score – solo probabilità storica", s["brier_hist"], "0.000")
        if "brier_mc" in s:
            put("Brier score – solo Time Machine", s["brier_mc"], "0.000",
                "La stima con il valore più basso è stata la più precisa finora")
        put("Margine medio ipotizzato", _pct(s["margin_mean"]), "0.00%")
        put("Guadagno reale medio per operazione", _pct(s["ret_mean"]), "0.00%")
        put("Guadagno netto medio (commissioni 0,1% + 0,1%)", _pct(s["net_mean"]), "0.00%")
        put("Guadagno netto mediano", _pct(s["net_median"]), "0.00%", "Metà delle operazioni ha fatto meglio, metà peggio")
        put(f"Risultato con {STAKE_EUR}€ per operazione", s["total_eur"], '+#,##0.00 "€";-#,##0.00 "€"',
            f"Somma dei guadagni netti investendo {STAKE_EUR}€ in ogni operazione chiusa")
        put("Profit factor", s["profit_factor"] if np.isfinite(s["profit_factor"]) else "∞", "0.00",
            "Somma dei guadagni / somma delle perdite: sopra 1 il sistema guadagna")
        put("Massimo ribasso del capitale (drawdown)", s["max_drawdown_eur"], '#,##0.00 "€"',
            "Peggior perdita accumulata dal punto più alto raggiunto")
        put("Miglior operazione", f"{s['best'][0]} {s['best'][1]:+.2f}%")
        put("Peggior operazione", f"{s['worst'][0]} {s['worst'][1]:+.2f}%")
        put("Durata media (ore)", s["hours_mean"], "0.0")
        put("Massimo guadagno toccato in media", _pct(s["mfe_mean"]), "0.00%",
            "Quanto è salito al massimo dopo l'acquisto (utile per capire se il SELL è troppo lontano)")
        put("Massima perdita toccata in media", _pct(s["mae_mean"]), "0.00%",
            "Quanto è sceso al massimo dopo l'acquisto (utile per capire se lo STOP è troppo vicino)")
    if s.get("direction_n"):
        put("Previsioni con prezzo salito dopo l'orizzonte", _pct(s["direction_up"]), "0%",
            "Considera tutte le previsioni, anche quelle con BUY non eseguito")
        put("Variazione media del prezzo dopo l'orizzonte", _pct(s["direction_mean"]), "0.00%")
    if not s.get("total"):
        put("Nessuna previsione ancora", "", None, "Il test registra le previsioni ad ogni controllo orario del bot")
    st.column_dimensions["A"].width, st.column_dimensions["B"].width, st.column_dimensions["C"].width = 44, 22, 90

    closed = df[df["status"].isin(CLOSED)] if not df.empty else df
    # -- Per crypto ----------------------------------------------------------- #
    pc = wb.create_sheet("Per crypto")
    header(pc, ["Crypto", "Operazioni chiuse", "Successo reale", "Probabilità media", "Guadagno medio",
                "Guadagno netto totale €"])
    if not closed.empty:
        for sym, g in closed.groupby("symbol"):
            pc.append([sym, len(g), (g["status"] == "win").mean(), g["probability"].mean(), g["ret_pct"].mean() / 100,
                       g["net_pct"].sum() * STAKE_EUR / 100])
    for col, fmt in (("C", "0%"), ("D", "0%"), ("E", "0.00%"), ("F", '+#,##0.00 "€";-#,##0.00 "€"')):
        for (cell,) in pc.iter_rows(min_row=2, min_col=ord(col) - 64, max_col=ord(col) - 64):
            cell.number_format = fmt
    for col in "ABCDEF":
        pc.column_dimensions[col].width = 18

    # -- Calibrazione --------------------------------------------------------- #
    cb = wb.create_sheet("Calibrazione")
    header(cb, ["Probabilità ipotizzata", "Operazioni chiuse", "Probabilità media", "Successo reale"])
    if not closed.empty:
        bins = pd.cut(closed["probability"], [0, 0.3, 0.4, 0.5, 0.6, 1.0], include_lowest=True,
                      labels=["fino al 30%", "30–40%", "40–50%", "50–60%", "oltre 60%"])
        for label, g in closed.groupby(bins, observed=True):
            cb.append([str(label), len(g), g["probability"].mean(), (g["status"] == "win").mean()])
    for (c1, c2) in cb.iter_rows(min_row=2, min_col=3, max_col=4):
        c1.number_format = c2.number_format = "0%"
    cb.append([])
    cb.append(["Se il successo reale segue la probabilità ipotizzata, le stime sono ben calibrate."])
    for col in "ABCD":
        cb.column_dimensions[col].width = 24

    # -- Andamento ------------------------------------------------------------ #
    an = wb.create_sheet("Andamento")
    header(an, ["Chiusura (UTC)", "Crypto", "Guadagno netto €", "Totale cumulato €"])
    if not closed.empty:
        total = 0.0
        for e in closed.sort_values("closed_at").to_dict("records"):
            total += e["net_pct"] * STAKE_EUR / 100
            an.append([e["closed_at"][:16].replace("T", " "), e["symbol"], e["net_pct"] * STAKE_EUR / 100, total])
        chart = LineChart()
        chart.title = f"Risultato cumulato con {STAKE_EUR}€ per operazione"
        chart.y_axis.title, chart.x_axis.title = "€", "Operazioni"
        chart.add_data(Reference(an, min_col=4, min_row=1, max_row=an.max_row), titles_from_data=True)
        chart.height, chart.width = 9, 22
        an.add_chart(chart, "F2")
    for col in "ABCD":
        an.column_dimensions[col].width = 20
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Esecuzione
# --------------------------------------------------------------------------- #

def _default_scan(exchange: str, quote: str, n: int, timeframe: str) -> pd.DataFrame:
    import crypto_core

    return crypto_core.scan(exchange, quote, n, timeframe)[0]


def _default_fetch(exchange: str, symbol: str, timeframe: str, since_ms: int) -> list:
    import crypto_core

    rows = crypto_core.get_exchange(exchange).fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
    return crypto_core.drop_open_candle(rows, timeframe)  # solo candele chiuse, come per i segnali


def run(cfg: dict, scan=_default_scan, fetch=_default_fetch) -> dict:
    """Un passo del test: aggiorna le previsioni aperte, registra le nuove, salva il file Excel.
    Se l'exchange non risponde, il file viene comunque riscritto con i dati già salvati."""
    ex, tf = cfg.get("test_exchange", "binance"), cfg.get("test_timeframe", "1h")
    journal = load_journal()
    closed = update_entries(journal, fetch)
    new, error = [], None
    try:
        radar = scan(ex, cfg.get("test_quote", "USDT"), int(cfg.get("test_coins", 30)), tf)
        new = record_signals(journal, radar, ex, tf)
    except Exception as e:  # noqa: BLE001 - exchange non raggiungibile
        error = f"{type(e).__name__}: {e}"[:300]
    save_journal(journal)
    write_excel(journal)
    stats = compute_stats(journal)
    log_run(f"{len(new)} nuove previsioni, {len(closed)} chiuse, totale {stats['total']}"
            + (f" | ERRORE: {error}" if error else " | ok"))
    return {"new": new, "closed": closed, "stats": stats, "error": error}


def log_run(text: str) -> None:
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} | {text}\n")
    except OSError:
        pass


def run_log_tail(lines: int = 20) -> str:
    try:
        return "\n".join(RUN_LOG.read_text(encoding="utf-8").splitlines()[-lines:])
    except OSError:
        return ""
