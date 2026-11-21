"""Visione del mercato, filtro di liquidità prima dell'acquisto e cache delle candele del Laboratorio."""
import pandas as pd
import pytest
from starlette.testclient import TestClient

from stocks.config import DEFAULT_CONFIG, load_config, save_config
from test_webapp import HOUR, Fakes, db, row  # noqa: F401 - db è una fixture
from webapp import autotrade, lab, market, server, store


def rows(n_up, n_down, rsi=50):
    out = [{"symbol": f"U{i}/USDC", "rsi": rsi, "trend_ema": "Rialzista", "change_24h": 1.0} for i in range(n_up)]
    out += [{"symbol": f"D{i}/USDC", "rsi": 30, "trend_ema": "Ribassista", "change_24h": -3.0} for i in range(n_down)]
    return out


def test_breadth_and_regime():
    b = market.breadth(rows(2, 8) + [{"symbol": "BTC/USDC", "rsi": 25, "trend_ema": "Ribassista",
                                       "change_24h": -4, "price": 68000}])
    assert b["coins"] == 11 and b["above_ema200"] == 2 and b["rsi_below_35"] == 9
    assert b["btc"]["price"] == 68000
    r = market.regime(b, {"value": 10})
    assert r["level"] == "ribasso" and "paura estrema" in r["text"]
    assert market.regime(market.breadth(rows(8, 2)), None)["level"] == "rialzo"
    assert market.breadth([]) is None


def test_external_sources_cached_and_fail_safe(db):  # noqa: F811
    calls = []

    def ok():
        calls.append(1)
        return {"data": [{"value": "10", "value_classification": "Extreme Fear"}, {"value": "30"}]}

    assert market.fear_greed(ok)["value"] == 10
    assert market.fear_greed(ok)["value"] == 10 and len(calls) == 1     # in cache: niente seconda chiamata

    store.kv_set("market_fng", {**store.kv_get("market_fng"), "at": 0})  # cache scaduta
    def down():
        raise ConnectionError("fonte giù")
    assert market.fear_greed(down)["value"] == 10                        # tiene l'ultimo valore


def test_market_api_uses_only_cache(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market, "_get_json", lambda url: pytest.fail("la pagina non deve aspettare la rete"))
    store.kv_set("radar", {"rows": rows(1, 3)})
    c = TestClient(server.create_app(), base_url="http://127.0.0.1:8765")
    d = c.get("/api/market").json()
    assert d["breadth"]["coins"] == 4 and d["regime"]["level"] == "ribasso"
    assert "market" in c.get("/api/overview").json()


def test_wide_spread_coin_is_not_bought(db):  # noqa: F811
    save_config({**load_config(), "strategia": "dip", "auto_attivo": True, "auto_modalita": "simulazione",
                 "auto_percentuale": 50, "auto_importo_min": 10, "auto_importo_max": 50, "auto_max_posizioni": 3,
                 "auto_max_trade_giorno": 10, "auto_spread_max": 0.5})
    store.add_deposit("simulazione", 100.0, "test")
    f = Fakes()
    eng = f.engine()
    spreads = {"AAA/USDT": 2.0, "BBB/USDT": 0.1}
    eng.trader.broker_factory = lambda cfg: autotrade.PaperBroker("bybiteu", last_price=lambda s: 100.0,
                                                                  spread=lambda s: spreads[s])
    sig = [{"symbol": s, "buy": 100.3, "sell": 106, "stop": 96, "score": 0} for s in spreads]
    bought = eng.trader.on_signals(load_config(), sig, 1_800_000_000_000)
    assert [p["symbol"] for p in bought] == ["BBB/USDT"]
    assert any("poco liquida" in e["text"] for e in store.events(limit=10))


def test_spread_of_order_book():
    assert autotrade.spread_of({"bids": [[99.9, 1]], "asks": [[100.1, 1]]}) == pytest.approx(0.2)
    assert autotrade.spread_of({"bids": [], "asks": [[1, 1]]}) is None


def test_lab_history_uses_disk_cache(tmp_path, monkeypatch):
    import crypto_core

    monkeypatch.setattr("stocks.config.DATA_DIR", tmp_path)
    clock = {"now": 1_800_000_000_000 // HOUR * HOUR}
    monkeypatch.setattr(lab.time, "time", lambda: clock["now"] / 1000)
    calls = []

    class Ex:
        def fetch_ohlcv(self, sym, timeframe, since, limit):
            calls.append(since)
            ts = [t for t in range(since // HOUR * HOUR, clock["now"] + HOUR, HOUR)][:limit]
            return [[t, 1, 1, 1, 1, 1] for t in ts]

    monkeypatch.setattr(crypto_core, "get_exchange", lambda ex: Ex())
    df = lab.history("bybiteu", "SOL/USDC", "1h", 2500)
    first = len(calls)
    assert first == 3 and len(df) >= 2499              # 3 blocchi da 1000
    calls.clear()
    clock["now"] += 3 * HOUR                           # 3 ore dopo
    df2 = lab.history("bybiteu", "SOL/USDC", "1h", 2500)
    assert len(calls) == 1 and abs(len(df2) - len(df)) <= 1   # dal disco: un solo blocco con le candele nuove
    assert df2["time"].iloc[-1] > df["time"].iloc[-1]
