"""Investimento automatico: acquisti dai segnali, limiti, SELL/STOP/scadenza, versamenti, API."""

from datetime import date

import pandas as pd
import pytest
from starlette.testclient import TestClient

from stocks.config import DEFAULT_CONFIG, load_config, save_config
from webapp import autotrade, engine, server, store

HOUR = 3_600_000
CLOSE = 1_800_000_000_000 // HOUR * HOUR
LOCAL = {"Host": "127.0.0.1:8765"}
POST = {**LOCAL, "X-Radar": "1"}


def sig(sym, score=60, price=100.0):
    return {"symbol": sym, "score": score, "probability": 55, "price": price, "buy": price * 0.99,
            "sell": price * 1.05, "stop": price * 0.97, "announced": False}


class Prices:
    def __init__(self):
        self.p = {}

    def __call__(self, sym):
        return self.p[sym]


@pytest.fixture
def env():
    store.DB_FILE.unlink(missing_ok=True)
    for ext in ("-wal", "-shm"):
        store.DB_FILE.with_name(store.DB_FILE.name + ext).unlink(missing_ok=True)
    store._initialized.clear()
    autotrade.delete_keys()
    save_config({**DEFAULT_CONFIG, "test_previsioni": False, "notifiche_desktop": False, "riepilogo_giornaliero": False,
                 "auto_attivo": True, "auto_percentuale": 50, "auto_importo_max": 400, "auto_importo_min": 10,
                 "auto_max_trade_giorno": 2, "auto_max_posizioni": 3, "auto_stop_perdita_giorno": 15})
    prices = Prices()
    sent = []
    trader = autotrade.AutoTrader(lambda cfg, level, title, text, short, symbol=None, push=True: sent.append(title),
                                  broker_factory=lambda cfg: autotrade.PaperBroker("binance", prices))
    yield trader, prices, sent
    save_config(dict(DEFAULT_CONFIG))


def test_trade_size_and_limits():
    cfg = {**DEFAULT_CONFIG, "auto_percentuale": 20, "auto_importo_max": 50, "auto_importo_min": 10, "auto_riserva": 0}
    assert autotrade.trade_size(cfg, 100) == (20.0, "")
    assert autotrade.trade_size(cfg, 1000)[0] == 50             # tetto per operazione
    assert autotrade.trade_size(cfg, 30)[0] == 10               # 20% = 6 < minimo, ma i soldi ci sono
    assert autotrade.trade_size(cfg, 8)[0] == 0                 # non bastano
    assert autotrade.trade_size({**cfg, "auto_riserva": 95}, 100)[0] == 0
    st = {"trades": 3, "open": 0, "pnl": 0}
    assert "acquisti al giorno" in autotrade.blocked_reason({**cfg, "auto_max_trade_giorno": 3}, st)
    st = {"trades": 0, "open": 0, "pnl": -21}
    assert "fino a domani" in autotrade.blocked_reason({**cfg, "auto_stop_perdita_giorno": 20}, st)


def test_buy_fill_sell_and_limits(env):
    trader, prices, sent = env
    store.add_deposit("simulazione", 1000)
    cfg = load_config()
    prices.p.update({"AAA/USDT": 100.0, "BBB/USDT": 10.0, "CCC/USDT": 5.0})
    bought = trader.on_signals(cfg, [sig("BBB/USDT", 55, 10), sig("AAA/USDT", 70), sig("CCC/USDT", 52, 5)], CLOSE)
    # i più forti per primi, massimo 2 acquisti al giorno; 50% della liquidità con tetto 400
    assert [p["symbol"] for p in bought] == ["AAA/USDT", "BBB/USDT"]
    assert bought[0]["cost"] == 400 and bought[1]["cost"] == 300
    assert any("non comprato" in t for t in sent)
    assert trader.on_signals(cfg, [sig("AAA/USDT", 70)], CLOSE) == []  # stesso segnale: mai due volte

    trader.tick(cfg, CLOSE + 60_000)  # prezzo sopra il BUY: ordine in attesa
    assert {p["status"] for p in store.positions("simulazione")} == {"ordine"}
    prices.p["AAA/USDT"] = 98.5
    out = trader.tick(cfg, CLOSE + 120_000)
    assert out["filled"] == ["AAA/USDT"]
    a = next(p for p in store.positions("simulazione") if p["symbol"] == "AAA/USDT")
    assert a["status"] == "aperta" and a["entry"] == 98.5 and a["sell_order"]

    prices.p["AAA/USDT"] = 106.0  # SELL raggiunto
    out = trader.tick(cfg, CLOSE + 180_000)
    assert out["closed"] == ["AAA/USDT"]
    a = next(p for p in store.positions("simulazione") if p["symbol"] == "AAA/USDT")
    assert a["status"] == "chiusa" and a["pnl"] > 0 and a["reason"].startswith("SELL")

    # BBB non scende mai al BUY: dopo 5 candele l'ordine si annulla senza spesa
    out = trader.tick(cfg, CLOSE + 5 * HOUR + 1)
    assert out["cancelled"] == ["BBB/USDT"]
    w = autotrade.wallet("simulazione")
    assert w["invested"] == 0 and w["cash"] == pytest.approx(1000 + a["pnl"])


def test_stop_and_daily_loss_limit(env):
    trader, prices, sent = env
    store.add_deposit("simulazione", 1000)
    cfg = load_config()
    prices.p["AAA/USDT"] = 99.0
    trader.on_signals(cfg, [sig("AAA/USDT")], CLOSE)
    trader.tick(cfg, CLOSE + 60_000)
    prices.p["AAA/USDT"] = 95.0  # sotto lo STOP (con un salto): vendita al mercato
    out = trader.tick(cfg, CLOSE + 120_000)
    p = store.positions("simulazione")[0]
    assert out["closed"] == ["AAA/USDT"] and p["reason"] == "STOP" and p["pnl"] < -15
    assert any("Limite di perdita" in t for t in sent)
    assert store.errors()[0]["category"] == "previsione"
    prices.p["BBB/USDT"] = 10.0
    assert trader.on_signals(cfg, [sig("BBB/USDT", 80, 10)], CLOSE + HOUR) == []  # fermo fino a domani


def test_expiry_close_all_and_inactive(env):
    trader, prices, sent = env
    store.add_deposit("simulazione", 200)
    cfg = load_config()
    assert trader.on_signals({**cfg, "auto_attivo": False}, [sig("AAA/USDT")], CLOSE) == []
    prices.p.update({"AAA/USDT": 99.0, "BBB/USDT": 9.9})
    trader.on_signals(cfg, [sig("AAA/USDT"), sig("BBB/USDT", 55, 10)], CLOSE)
    trader.tick(cfg, CLOSE + 60_000)
    out = trader.tick(cfg, CLOSE + 60_000 + 48 * HOUR + 1)  # orizzonte del piano finito
    assert set(out["closed"]) == {"AAA/USDT", "BBB/USDT"}
    assert all(p["reason"] == "scadenza del piano" for p in store.positions("simulazione"))

    prices.p["CCC/USDT"] = 4.95
    assert trader.on_signals({**cfg, "auto_max_trade_giorno": 5}, [sig("CCC/USDT", 60, 5)], CLOSE + 50 * HOUR)
    autotrade.request_close_all("simulazione")
    out = trader.tick(cfg, CLOSE + 50 * HOUR + 60_000)
    assert out["closed"] == ["CCC/USDT"] and store.positions("simulazione")[0]["reason"] == "chiusa da te"


def test_deposit_plan(env):
    trader, prices, sent = env
    cfg = {**load_config(), "auto_piano_importo": 100, "auto_piano_frequenza": "mensile", "auto_piano_giorno": 5}
    assert not trader.deposit_plan(cfg, date(2026, 11, 4))
    assert trader.deposit_plan(cfg, date(2026, 11, 5))
    assert not trader.deposit_plan(cfg, date(2026, 11, 5))  # una sola volta al giorno
    assert autotrade.wallet("simulazione")["deposited"] == 100
    weekly = {**cfg, "auto_piano_frequenza": "settimanale", "auto_piano_giorno": 1}
    assert trader.deposit_plan(weekly, date(2026, 11, 9))  # lunedì
    real = {**cfg, "auto_modalita": "reale"}
    assert trader.deposit_plan(real, date(2026, 12, 5))     # in reale solo promemoria
    assert autotrade.wallet("reale")["deposited"] == 0 and "Promemoria" in sent[-1]


def test_engine_buys_confirmed_signals(env):
    trader, prices, sent = env
    store.add_deposit("simulazione", 500)
    prices.p["EEE/USDT"] = 50.0
    final = pd.DataFrame([{**sig("EEE/USDT", 70, 50), "prev_score": 10, "base": "EEE", "signal": "x",
                           "upside_pct": 6, "risk_pct": 2, "bt_trades": 10, "bt_wins": 5, "bt_win_rate": 50.0,
                           "bt_avg_ret": 1.0, "mc_win": 45.0, "mc_verdict": "vantaggio", "rsi": 60.0, "vol_ratio": 1.2,
                           "change_24h": 1.0, "volume_24h": 1e9, "last_candle": CLOSE - HOUR, "alerts": ""}])
    eng = engine.SignalEngine(scan=lambda cfg: (final, []), provisional_scan=lambda cfg: (pd.DataFrame(), []),
                              fetch=lambda *a: [], notify=lambda *a: [],
                              broker_factory=lambda cfg: autotrade.PaperBroker("binance", prices))
    out = eng.confirm(load_config(), CLOSE)
    assert out["signals"] == ["EEE/USDT"] and out["bought"] == ["EEE/USDT"]


def test_api_safety(env):
    c = TestClient(server.create_app())
    r = c.get("/api/auto", headers=LOCAL)
    assert r.status_code == 200 and r.json()["status"]["mode"] == "simulazione"
    assert r.json()["status"]["keys"] == {"configured": False}
    assert c.post("/api/auto/stop", json={}, headers=LOCAL).status_code == 403  # senza X-Radar
    # modalità reale: servono chiavi verificate e la parola di conferma
    assert "chiavi" in c.post("/api/auto/mode", json={"mode": "reale", "confirm": "INVESTI"}, headers=POST).json()["error"]
    autotrade.save_keys("binance", "k" * 20 + "ABCD", "s" * 30, info={"verified": "2026-11-10T10:00:00", "withdraw": False})
    keys = c.get("/api/auto?live=0", headers=LOCAL).json()["status"]["keys"]
    assert keys["key_hint"] == "…ABCD" and "s" * 30 not in str(keys)            # le chiavi non escono mai
    assert c.post("/api/auto/mode", json={"mode": "reale", "confirm": "no"}, headers=POST).status_code == 400
    assert c.post("/api/auto/mode", json={"mode": "reale", "confirm": "investi"}, headers=POST).status_code == 200
    cfg = load_config()
    assert cfg["auto_modalita"] == "reale" and cfg["auto_attivo"] is False     # si riparte sempre da fermo
    assert "INVESTI" not in str(load_config())
    assert c.post("/api/auto/settings", json={"auto_percentuale": 500}, headers=POST).status_code == 400
    assert c.post("/api/auto/settings", json={"auto_max_trade_giorno": 5}, headers=POST).json()["changed"] == \
        ["auto_max_trade_giorno"]
    assert c.post("/api/auto/deposit", json={"amount": "nan"}, headers=POST).status_code == 400
    assert c.post("/api/auto/deposit", json={"amount": 50, "mode": "simulazione"}, headers=POST).status_code == 200
    r = c.post("/api/auto/stop", json={"close_all": True}, headers=POST).json()
    assert r["close_all"] and load_config()["auto_attivo"] is False
    assert store.kv_get("auto_command")["action"] == "close_all"
    c.post("/api/auto/keys", json={"delete": True}, headers=POST)
    assert load_config()["auto_modalita"] == "simulazione" and not autotrade.keys_public()["configured"]


class FakeExchange:
    """ccxt finto: registra gli ordini inviati."""

    def __init__(self):
        self.orders, self.calls, self.free, self.last = {}, [], {"USDT": 1000.0, "AAA": 0.0}, 100.0

    def market(self, sym):
        return {"base": sym.split("/")[0], "limits": {"cost": {"min": 5}}}

    def amount_to_precision(self, sym, x):
        return f"{int(x * 1000 + 1e-6) / 1000:.3f}"

    def price_to_precision(self, sym, x):
        return f"{x:.2f}"

    def fetch_balance(self):
        return {"free": dict(self.free)}

    def fetch_ticker(self, sym):
        return {"last": self.last}

    def _order(self, kind, sym, qty, price, status="open"):
        oid = f"o{len(self.orders) + 1}"
        self.orders[oid] = {"id": oid, "kind": kind, "status": status, "filled": 0, "amount": qty, "price": price}
        self.calls.append((kind, sym, qty, price))
        return self.orders[oid]

    def create_limit_buy_order(self, sym, qty, price, params=None):
        return self._order("buy", sym, qty, price)

    def create_limit_sell_order(self, sym, qty, price):
        return self._order("sell", sym, qty, price)

    def create_market_sell_order(self, sym, qty):
        o = self._order("market_sell", sym, qty, None, "closed")
        o.update(average=self.last, cost=qty * self.last)
        return o

    def cancel_order(self, oid, sym):
        self.calls.append(("cancel", oid))
        self.orders[oid]["status"] = "canceled"

    def fetch_order(self, oid, sym):
        return self.orders[oid]


def test_live_broker_orders(env):
    _, _, sent = env
    fx = FakeExchange()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = autotrade.AutoTrader(lambda *a, **k: sent.append(a[2]), broker_factory=lambda cfg: autotrade.LiveBroker(fx))
    cfg = load_config()
    trader.on_signals(cfg, [sig("AAA/USDT")], CLOSE)
    assert fx.calls[0] == ("buy", "AAA/USDT", 4.04, 99.0)  # 50% di 1000 con tetto 400 -> 400/99, arrotondato
    fx.orders["o1"].update(status="closed", filled=4.04, average=99.0, cost=399.96)
    fx.free["AAA"] = 4.036  # commissione trattenuta sulla coin
    trader.tick(cfg, CLOSE + 60_000)
    p = store.positions("reale")[0]
    assert p["status"] == "aperta" and p["qty"] == 4.036 and fx.calls[1] == ("sell", "AAA/USDT", 4.036, 105.0)
    fx.last = 96.5  # STOP: annulla il SELL sull'exchange e vende al mercato
    trader.tick(cfg, CLOSE + 120_000)
    assert ("cancel", "o2") in fx.calls and fx.calls[-1][0] == "market_sell"
    p = store.positions("reale")[0]
    assert p["status"] == "chiusa" and p["reason"] == "STOP" and p["pnl"] < 0
