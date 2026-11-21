"""Radar Web: motore dei segnali (preavviso / conferma / operazioni), report e server locale."""

from datetime import datetime, timezone

import pandas as pd
import pytest
from starlette.testclient import TestClient

from stocks.config import DEFAULT_CONFIG, save_config
from webapp import engine, reports, server, store

HOUR = 3_600_000


def row(sym, score, prev, prob, price=100.0, last_candle=0):
    return {"symbol": sym, "base": sym.split("/")[0], "price": price, "change_24h": 1.0, "volume_24h": 1e9,
            "score": score, "prev_score": prev, "probability": prob, "signal": "🟢 FORTE RIALZO", "rsi": 60.0,
            "vol_ratio": 1.2, "volatility": 1.0, "trend_ema": "Rialzista", "alerts": "Breakout massimi 20",
            "buy": price * 0.99, "sell": price * 1.05, "stop": price * 0.97, "upside_pct": 6.06, "risk_pct": 2.02,
            "bt_trades": 10, "bt_wins": 5, "bt_win_rate": 50.0, "bt_avg_ret": 1.0, "mc_win": 45.0,
            "mc_verdict": "vantaggio", "last_candle": last_candle, "spark": [1.0, 2.0, 3.0]}


@pytest.fixture
def db():
    store.DB_FILE.unlink(missing_ok=True)
    for ext in ("-wal", "-shm"):
        store.DB_FILE.with_name(store.DB_FILE.name + ext).unlink(missing_ok=True)
    store._initialized.clear()
    save_config({**DEFAULT_CONFIG, "test_previsioni": False, "notifiche_desktop": False,
                 "riepilogo_giornaliero": False})
    yield
    save_config(dict(DEFAULT_CONFIG))


class Fakes:
    def __init__(self):
        self.pre = pd.DataFrame()
        self.final = pd.DataFrame()
        self.sent = []
        self.calls = []
        self.candles = {}

    def provisional(self, cfg):
        self.calls.append("pre")
        return self.pre, []

    def scan(self, cfg):
        self.calls.append("scan")
        return self.final, ["ZZZ/USDT: timeout"]

    def notify(self, cfg, title, text, short):
        self.sent.append(title)
        return ["Telegram: inviato"]

    def fetch(self, ex, sym, tf, since):
        return self.candles.get(sym, [])

    def engine(self):
        return engine.SignalEngine(scan=self.scan, provisional_scan=self.provisional, fetch=self.fetch,
                                   notify=self.notify)


def test_prealert_then_confirm(db):
    f = Fakes()
    eng = f.engine()
    close = 1_800_000_000_000 // HOUR * HOUR
    f.pre = pd.DataFrame([row("AAA/USDT", 55, 30, 50), row("BBB/USDT", 47, 20, 45),
                          row("CCC/USDT", 60, 55, 50), row("DDD/USDT", 30, 20, 60), row("LOW/USDT", 60, 10, 20)])
    sent = eng.prealert(load(), close, close - 20 * 60_000)
    assert {s["symbol"] for s in sent} == {"AAA/USDT", "BBB/USDT"}  # CCC era già al massimo, LOW prob. bassa
    assert all("PREAVVISO" in t for t in f.sent) and "Tra ~20 minuti" in store.events()[-1]["text"]
    assert eng.prealert(load(), close, close - 19 * 60_000) == []  # stessa candela: niente doppioni

    f.final = pd.DataFrame([row("AAA/USDT", 52, 30, 48, last_candle=close - HOUR),
                            row("BBB/USDT", 40, 20, 45, last_candle=close - HOUR),
                            row("EEE/USDT", 70, 10, 60, price=50, last_candle=close - HOUR)])
    out = eng.confirm(load(), close)
    assert out["confirmed"] == ["AAA/USDT"] and out["cancelled"] == ["BBB/USDT"]
    assert set(out["signals"]) == {"AAA/USDT", "EEE/USDT"}
    pre = {s["symbol"]: s["status"] for s in store.signals(kind="preavviso")}
    assert pre == {"AAA/USDT": "confermato", "BBB/USDT": "annullato"}
    errs = store.errors()
    assert any(e["category"] == "previsione" and e["symbol"] == "BBB/USDT" for e in errs)   # preavviso sbagliato
    assert any(e["category"] == "tecnico" and e["symbol"] == "ZZZ/USDT" for e in errs)     # errore della scansione
    assert sum("BUY MASSIMO" in t for t in f.sent) == 2
    ops = store.load_operations()
    assert {o["symbol"] for o in ops} == {"AAA/USDT", "EEE/USDT"} and all(o["status"] == "pending" for o in ops)
    assert store.kv_get("radar")["rows"][0]["symbol"] == "AAA/USDT"

    # stessa coin di nuovo al massimo: in pausa (pausa_stesso_simbolo_ore)
    assert eng.confirm(load(), close + HOUR)["signals"] == []


def test_operation_closes_at_sell_and_stop(db):
    f = Fakes()
    eng = f.engine()
    close = 1_800_000_000_000 // HOUR * HOUR
    ts = close - HOUR
    f.final = pd.DataFrame([row("WIN/USDT", 60, 10, 60, last_candle=ts), row("LOS/USDT", 60, 10, 60, last_candle=ts)])
    eng.confirm(load(), close)
    # WIN: BUY eseguito alla candela 1, SELL (105) toccato alla 2. LOS: STOP (97) alla 2.
    f.candles["WIN/USDT"] = [[ts, 100, 100, 100, 100, 1], [ts + HOUR, 100, 100, 98.5, 99, 1],
                             [ts + 2 * HOUR, 100, 106, 99.5, 105, 1]]
    f.candles["LOS/USDT"] = [[ts, 100, 100, 100, 100, 1], [ts + HOUR, 100, 100, 98.5, 99, 1],
                             [ts + 2 * HOUR, 99, 99, 96, 96.5, 1]]
    f.final = pd.DataFrame()
    out = eng.confirm(load(), close + HOUR)
    assert set(out["closed_ops"]) == {"WIN/USDT", "LOS/USDT"}
    st = {o["symbol"]: o["status"] for o in store.load_operations()}
    assert st == {"WIN/USDT": "win", "LOS/USDT": "loss"}
    assert any(e["category"] == "previsione" and e["symbol"] == "LOS/USDT" and "STOP" in e["message"]
               for e in store.errors())
    s = reports.statistics()
    assert s["summary"]["closed"] == 2 and len(s["equity"]) == 2 and s["per_coin"]


def test_tick_schedule(db):
    f = Fakes()
    eng = f.engine()
    base = 1_800_000_000_000 // HOUR * HOUR / 1000  # inizio di un'ora, in secondi
    eng.tick(base + 41 * 60)                 # primo giro: conferma della candela chiusa + preavviso
    assert f.calls == ["scan", "pre"]
    eng.tick(base + 45 * 60)                 # stessa candela: niente
    assert f.calls == ["scan", "pre"]
    eng.tick(base + 3600 + 5)                # chiusa da 5 s: si aspetta ancora la pubblicazione della candela
    assert f.calls == ["scan", "pre"]
    eng.tick(base + 3600 + 30)               # conferma
    assert f.calls == ["scan", "pre", "scan"]
    st = engine.status()
    assert st["next_close"] == int((base + 7200) * 1000) and st["next_prealert"] == st["next_close"] - 20 * 60_000
    store.kv_set("force_scan", True)         # pulsante "scansiona ora"
    eng.tick(base + 3600 + 60)
    assert f.calls[-1] == "scan"


def test_daily_recap_once(db):
    save_config({**DEFAULT_CONFIG, "riepilogo_giornaliero": True, "riepilogo_ora": "21:00", "notifiche_desktop": False})
    f = Fakes()
    eng = f.engine()
    state = {}
    assert not eng.maybe_recap(load(), state, datetime(2026, 11, 10, 20, 59))
    assert eng.maybe_recap(load(), state, datetime(2026, 11, 10, 21, 0))
    assert not eng.maybe_recap(load(), state, datetime(2026, 11, 10, 22, 0))
    assert f.sent and "Recap" in f.sent[-1]


def test_calendar_and_recap(db):
    store.add_error("tecnico", "scansione", "Exchange non raggiungibile", when=datetime(2026, 11, 3, 10, 0))
    store.add_error("previsione", "preavviso", "Preavviso non confermato", symbol="X/USDT",
                    when=datetime(2026, 11, 3, 11, 0))
    assert not store.add_error("tecnico", "scansione", "Exchange non raggiungibile",
                               when=datetime(2026, 11, 3, 10, 0))  # stesso errore: una volta sola
    cal = reports.month_calendar("2026-11")
    assert cal["days"]["2026-11-03"]["tech"] == 1 and cal["days"]["2026-11-03"]["pred"] == 1
    assert cal["totals"]["tech"] == 1 and cal["n_days"] == 30
    rec = reports.day_recap(datetime(2026, 11, 3).date())
    assert rec["errors_tech"] == 1 and rec["errors_pred"] == 1
    assert "Errori: 1 di previsione · 1 tecnici" in reports.recap_text(rec)


def test_ingest_logs(db):
    from stocks.background import LOG_FILE

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    LOG_FILE.write_text(f"{now:%Y-%m-%d %H:%M:%S},123 ERROR Controllo orario fallito\n"
                        f"{now:%Y-%m-%d %H:%M:%S},456 INFO tutto ok\n"
                        f"{now:%Y-%m-%d %H:%M:%S},789 WARNING Telegram: Conflict\n", encoding="utf-8")
    assert engine.ingest_logs() == 2
    assert engine.ingest_logs() == 0
    LOG_FILE.unlink()


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #

@pytest.fixture
def client(db):
    return TestClient(server.create_app(), base_url="http://127.0.0.1:8765")


def test_pages_and_api(client):
    f = Fakes()
    eng = f.engine()
    close = int(datetime.now(timezone.utc).timestamp() * 1000) // HOUR * HOUR
    f.final = pd.DataFrame([row("EEE/USDT", 70, 10, 60, last_candle=close - HOUR)])
    eng.confirm(load(), close)
    assert "Radar Web" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    o = client.get("/api/overview").json()
    assert o["today"]["massimi"] == 1 and o["signals_today"][0]["symbol"] == "EEE/USDT"
    assert client.get("/api/signals?days=1").json()["signals"]
    assert client.get("/api/operations").json()["summary"]["total"] == 1
    assert "equity" in client.get("/api/stats?source=test").json()
    assert "text" in client.get("/api/recap").json()
    assert client.get("/api/calendar?month=2026-11").json()["month"] == "2026-11"
    assert client.get("/api/calendar?month=boh").status_code == 400
    assert client.get("/api/events").json()["last"] >= 1
    csv = client.get("/api/export/segnali.csv")
    assert csv.status_code == 200 and "EEE/USDT" in csv.text


def test_settings_protected(client):
    assert client.post("/api/settings", json={"preavviso_minuti": 15}).status_code == 403   # senza X-Radar
    h = {"X-Radar": "1"}
    r = client.post("/api/settings", json={"preavviso_minuti": 15, "sconosciuta": 1}, headers=h)
    assert r.json()["changed"] == ["preavviso_minuti"]
    assert client.get("/api/settings").json()["settings"]["preavviso_minuti"] == 15
    assert client.post("/api/settings", json={"preavviso_minuti": 500}, headers=h).status_code == 400
    assert client.post("/api/settings", json={"segnali_exchange": "evil"}, headers=h).status_code == 400
    assert client.post("/api/settings", json={"riepilogo_ora": "25:99"}, headers=h).status_code == 400
    assert "telegram_token" not in client.get("/api/settings").json()["settings"]


def test_foreign_host_blocked(db):
    c = TestClient(server.create_app(), base_url="http://evil.example.com")
    assert c.get("/api/overview").status_code == 403


def test_bot_commands(db):
    from stocks import telegram_bot as tb

    from test_bot import FakeAPI

    f = Fakes()
    close = int(datetime.now(timezone.utc).timestamp() * 1000) // HOUR * HOUR
    f.final = pd.DataFrame([row("EEE/USDT", 70, 10, 60, last_candle=close - HOUR)])
    f.engine().confirm(load(), close)
    b = tb.RadarBot(FakeAPI())
    assert "EEE/USDT" in b.dispatch("segnali", "", 42)
    assert "Segnali BUY massimo: 1" in b.dispatch("recap", "", 42)
    assert b.parse("🟢 Segnali") == ("segnali", "")


def load():
    from stocks.config import load_config

    return load_config()
