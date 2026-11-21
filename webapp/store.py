"""Archivio SQLite dell'app web: segnali, operazioni dei segnali BUY massimo, errori, eventi (notifiche).

Il file è RadarUltimate/radar_web.db: lo usano sia il server web sia il bot (stesso motore dei segnali),
per questo ogni chiamata apre la sua connessione (SQLite in modalità WAL regge più processi).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from stocks.config import DATA_DIR

DB_FILE = DATA_DIR / "radar_web.db"
_INIT_LOCK = threading.Lock()
_initialized: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,            -- ISO UTC
    kind TEXT NOT NULL,               -- preavviso | massimo | avvicinamento
    status TEXT NOT NULL DEFAULT '',  -- preavviso: attesa | confermato | annullato
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_close INTEGER NOT NULL,    -- ms UTC della chiusura della candela a cui si riferisce
    score REAL, prev_score REAL, probability REAL, price REAL,
    buy REAL, sell REAL, stop REAL, upside_pct REAL, risk_pct REAL,
    details TEXT                      -- JSON con il resto (allerte, statistica, verdetto Time Machine…)
);
CREATE INDEX IF NOT EXISTS ix_signals_created ON signals(created);
CREATE INDEX IF NOT EXISTS ix_signals_candle ON signals(candle_close, kind);

CREATE TABLE IF NOT EXISTS operations (   -- operazioni simulate dei segnali BUY massimo (stesso formato del test)
    id TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                 -- ISO locale
    day TEXT NOT NULL,                -- YYYY-MM-DD locale (per il calendario)
    category TEXT NOT NULL,           -- tecnico | previsione
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    symbol TEXT,
    ref TEXT UNIQUE                   -- chiave per non registrare due volte lo stesso errore
);
CREATE INDEX IF NOT EXISTS ix_errors_day ON errors(day);

CREATE TABLE IF NOT EXISTS events (       -- feed attività + notifiche per il browser
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,              -- massimo | preavviso | info | errore | recap
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    symbol TEXT
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS positions (    -- investimento automatico: una riga per ogni acquisto
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,            -- ISO UTC
    day TEXT NOT NULL,                -- YYYY-MM-DD locale (limiti giornalieri)
    mode TEXT NOT NULL,               -- simulazione | reale
    status TEXT NOT NULL,             -- ordine | aperta | chiusa | annullata | errore
    symbol TEXT NOT NULL,
    ref TEXT UNIQUE,                  -- modalità|simbolo|candela: mai due acquisti per lo stesso segnale
    closed_day TEXT,
    pnl REAL,
    data TEXT NOT NULL                -- JSON con prezzi, ordini, quantità
);
CREATE INDEX IF NOT EXISTS ix_positions_mode ON positions(mode, status);

CREATE TABLE IF NOT EXISTS deposits (     -- versamenti (in simulazione sono il capitale virtuale)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS wallet_days (  -- valore del capitale a fine giornata (grafico)
    day TEXT NOT NULL,
    mode TEXT NOT NULL,
    deposits REAL NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (day, mode)
);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or now_utc()).isoformat(timespec="seconds")


@contextmanager
def connect():
    path = str(DB_FILE)
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        with _INIT_LOCK:
            if path not in _initialized:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                _initialized.add(path)
        yield con
        con.commit()
    finally:
        con.close()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# -- chiave/valore ------------------------------------------------------------ #
def kv_get(key: str, default=None):
    with connect() as con:
        r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return default


def kv_set(key: str, value) -> None:
    with connect() as con:
        con.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False, default=str)))


# -- segnali ------------------------------------------------------------------ #
SIGNAL_COLS = ("score", "prev_score", "probability", "price", "buy", "sell", "stop", "upside_pct", "risk_pct")


def add_signal(kind: str, symbol: str, exchange: str, timeframe: str, candle_close: int, status: str = "",
               details: dict | None = None, **values) -> int:
    cols = {k: values.get(k) for k in SIGNAL_COLS}
    with connect() as con:
        cur = con.execute(
            f"INSERT INTO signals(created, kind, status, symbol, exchange, timeframe, candle_close, "
            f"{', '.join(cols)}, details) VALUES(?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(cols))}, ?)",
            (iso_utc(), kind, status, symbol, exchange, timeframe, int(candle_close), *cols.values(),
             json.dumps(details or {}, ensure_ascii=False, default=str)))
        return int(cur.lastrowid)


def signals(since_iso: str | None = None, kind: str | None = None, candle_close: int | None = None,
            limit: int = 500) -> list[dict]:
    q, args = "SELECT * FROM signals WHERE 1=1", []
    if since_iso:
        q += " AND created >= ?"
        args.append(since_iso)
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    if candle_close is not None:
        q += " AND candle_close = ?"
        args.append(int(candle_close))
    q += " ORDER BY created DESC, id DESC LIMIT ?"
    args.append(limit)
    with connect() as con:
        out = _rows(con.execute(q, args))
    for r in out:
        try:
            r["details"] = json.loads(r["details"] or "{}")
        except ValueError:
            r["details"] = {}
    return out


def set_signal_status(signal_id: int, status: str) -> None:
    with connect() as con:
        con.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))


def last_signal_time(symbol: str, kind: str) -> str | None:
    with connect() as con:
        r = con.execute("SELECT MAX(created) AS t FROM signals WHERE symbol=? AND kind=?", (symbol, kind)).fetchone()
    return r["t"] if r else None


# -- operazioni (formato identico al registro di previsioni.py) -------------- #
def load_operations() -> list[dict]:
    with connect() as con:
        return [json.loads(r["data"]) for r in con.execute("SELECT data FROM operations ORDER BY created")]


def save_operations(ops: list[dict]) -> None:
    with connect() as con:
        con.executemany(
            "INSERT INTO operations(id, created, data) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            [(o["id"], o["created"], json.dumps(o, ensure_ascii=False, default=str)) for o in ops])


# -- errori ------------------------------------------------------------------- #
def add_error(category: str, source: str, message: str, symbol: str | None = None, ref: str | None = None,
              when: datetime | None = None) -> bool:
    """Registra un errore (una sola volta per `ref`). Ritorna True se è nuovo."""
    when = when or datetime.now()
    if when.tzinfo is not None:  # ora UTC -> ora locale (il calendario è nel fuso del PC)
        when = when.astimezone().replace(tzinfo=None)
    ref = ref or hashlib.sha1(f"{category}|{source}|{message}|{when:%Y-%m-%d %H:%M}".encode()).hexdigest()
    with connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO errors(ts, day, category, source, message, symbol, ref) VALUES(?,?,?,?,?,?,?)",
            (when.strftime("%Y-%m-%dT%H:%M:%S"), when.strftime("%Y-%m-%d"), category, source, message[:600],
             symbol, ref))
        return cur.rowcount > 0


def errors(day: str | None = None, month: str | None = None, limit: int = 1000) -> list[dict]:
    q, args = "SELECT * FROM errors WHERE 1=1", []
    if day:
        q += " AND day = ?"
        args.append(day)
    if month:
        q += " AND day LIKE ?"
        args.append(f"{month}-%")
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with connect() as con:
        return _rows(con.execute(q, args))


# -- eventi ------------------------------------------------------------------- #
def add_event(level: str, title: str, text: str, symbol: str | None = None) -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO events(ts, level, title, text, symbol) VALUES(?,?,?,?,?)",
                          (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), level, title, text, symbol))
        return int(cur.lastrowid)


def events(after: int = 0, limit: int = 100) -> list[dict]:
    with connect() as con:
        if after:
            return _rows(con.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after, limit)))
        return list(reversed(_rows(con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)))))


def last_event_id() -> int:
    with connect() as con:
        r = con.execute("SELECT MAX(id) AS m FROM events").fetchone()
    return int(r["m"] or 0)


# -- investimento automatico -------------------------------------------------- #
POSITION_COLS = ("id", "created", "day", "mode", "status", "symbol", "ref", "closed_day", "pnl")


def _position(r) -> dict:
    d = json.loads(r["data"])
    d.update({k: r[k] for k in POSITION_COLS})
    return d


def add_position(p: dict) -> int | None:
    """Nuova posizione. None se esiste già (stesso `ref`): protegge dai doppi acquisti."""
    data = {k: v for k, v in p.items() if k not in POSITION_COLS}
    with connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO positions(created, day, mode, status, symbol, ref, closed_day, pnl, data) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (p.get("created") or iso_utc(), p.get("day") or datetime.now().strftime("%Y-%m-%d"), p["mode"],
             p["status"], p["symbol"], p.get("ref"), p.get("closed_day"), p.get("pnl"),
             json.dumps(data, ensure_ascii=False, default=str)))
        return int(cur.lastrowid) if cur.rowcount else None


def save_position(p: dict) -> None:
    data = {k: v for k, v in p.items() if k not in POSITION_COLS}
    with connect() as con:
        con.execute("UPDATE positions SET status=?, closed_day=?, pnl=?, data=? WHERE id=?",
                    (p["status"], p.get("closed_day"), p.get("pnl"),
                     json.dumps(data, ensure_ascii=False, default=str), p["id"]))


def positions(mode: str | None = None, status: tuple | list | None = None, limit: int = 2000) -> list[dict]:
    q, args = "SELECT * FROM positions WHERE 1=1", []
    if mode:
        q += " AND mode = ?"
        args.append(mode)
    if status:
        q += f" AND status IN ({','.join('?' * len(status))})"
        args.extend(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as con:
        return [_position(r) for r in con.execute(q, args)]


def add_deposit(mode: str, amount: float, note: str = "", when: datetime | None = None) -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO deposits(ts, mode, amount, note) VALUES(?,?,?,?)",
                          ((when or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S"), mode, float(amount), note))
        return int(cur.lastrowid)


def deposits(mode: str) -> list[dict]:
    with connect() as con:
        return _rows(con.execute("SELECT * FROM deposits WHERE mode=? ORDER BY ts DESC, id DESC", (mode,)))


def delete_deposit(dep_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM deposits WHERE id=?", (dep_id,))


def set_wallet_day(day: str, mode: str, deposits_total: float, value: float) -> None:
    with connect() as con:
        con.execute("INSERT INTO wallet_days(day, mode, deposits, value) VALUES(?,?,?,?) "
                    "ON CONFLICT(day, mode) DO UPDATE SET deposits=excluded.deposits, value=excluded.value",
                    (day, mode, deposits_total, value))


def wallet_days(mode: str) -> list[dict]:
    with connect() as con:
        return _rows(con.execute("SELECT * FROM wallet_days WHERE mode=? ORDER BY day", (mode,)))
