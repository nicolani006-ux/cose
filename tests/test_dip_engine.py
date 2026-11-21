"""Motore dei segnali con la strategia 'Compra il ribasso' (dip)."""
import pandas as pd

from stocks.config import load_config, save_config
from test_webapp import HOUR, Fakes, db, row  # noqa: F401 - db è una fixture
from webapp import store


def dip_row(sym, hit, price=100.0, rsi=33.0):
    r = row(sym, -20, -10, 20, price=price)   # punteggio basso: con la strategia trend non sarebbe un segnale
    r.update(rsi=rsi, dip_hit=hit, dip_buy=price * 1.003, dip_stop=price * 0.96, dip_sell=price * 1.06,
             dip_upside_pct=5.7, dip_risk_pct=4.3)
    return r


def test_dip_strategy_signals_and_buys(db):  # noqa: F811
    save_config({**load_config(), "strategia": "dip", "dip_rsi": 35, "auto_attivo": True,
                 "auto_modalita": "simulazione", "auto_percentuale": 50, "auto_importo_min": 10,
                 "auto_importo_max": 50, "auto_max_posizioni": 3, "auto_max_trade_giorno": 10,
                 "dip_candele_max": 120})
    store.add_deposit("simulazione", 100.0, "test")
    f = Fakes()
    eng = f.engine()
    eng.trader.broker_factory = lambda cfg: __import__("webapp.autotrade", fromlist=["PaperBroker"]).PaperBroker(
        "bybiteu", last_price=lambda sym: 100.0)
    close = 1_800_000_000_000 // HOUR * HOUR

    f.pre = pd.DataFrame([dip_row("AAA/USDT", False, rsi=38), dip_row("BBB/USDT", False, rsi=60)])
    assert eng.prealert(load_config(), close, close - 20 * 60_000) == []          # niente preavvisi
    assert [r["symbol"] for r in store.kv_get("watch")["rows"]] == ["AAA/USDT"]    # RSI vicino alla soglia

    f.final = pd.DataFrame([dip_row("AAA/USDT", True), dip_row("BBB/USDT", False), dip_row("CCC/USDT", True, price=50)])
    out = eng.confirm(load_config(), close)
    assert set(out["signals"]) == {"AAA/USDT", "CCC/USDT"}
    assert any("COMPRA IL RIBASSO" in t for t in f.sent)
    sig = {s["symbol"]: s for s in store.signals(kind="massimo")}
    assert sig["AAA/USDT"]["buy"] == 100.0 * 1.003 and sig["AAA/USDT"]["stop"] == 96.0
    pos = store.positions("simulazione")
    assert {p["symbol"] for p in pos} == {"AAA/USDT", "CCC/USDT"}
    assert all(p["buy"] > p["stop"] and p["sell"] > p["buy"] for p in pos)
    assert all(p["horizon"] == 120 for p in pos)   # durata massima scelta per la strategia dip

    # con la strategia trend le stesse righe (punteggio -20) non danno segnali
    save_config({**load_config(), "strategia": "trend", "pausa_stesso_simbolo_ore": 0})
    f.final = pd.DataFrame([dip_row("DDD/USDT", True)])
    assert eng.confirm(load_config(), close + HOUR)["signals"] == []


def test_scan_failure_does_not_stop_stop_checks(db):  # noqa: F811
    f = Fakes()
    eng = f.engine()
    ticks = []

    def broken_confirm(cfg, open_ms):   # un errore qualsiasi durante la conferma della candela
        raise ConnectionError("exchange irraggiungibile")

    eng.confirm = broken_confirm
    eng.trader.tick = lambda cfg, now_ms: ticks.append(now_ms) or {}
    t = 1_800_000_000          # 1 minuto dopo la chiusura di una candela: si conferma
    t = (t * 1000 // HOUR * HOUR + 60_000) / 1000
    eng.tick(t)
    eng.tick(t + 15)           # entro il minuto di attesa: niente nuova scansione, ma STOP/SELL controllati
    assert len(ticks) == 2
    assert any("scansione non riuscita" in e["message"] for e in store.errors())


def test_rimbalzo_strategy_signals_and_buys_in_simulation(db):  # noqa: F811
    from webapp import lab, lab_rimbalzo
    from webapp.autotrade import PaperBroker, plan_horizon

    save_config({**load_config(), "strategia": "rimbalzo", "rimb_banda": 3.5, "rimb_stop_atr": 2.0,
                 "rimb_candele_max": 24, "auto_attivo": True, "auto_modalita": "simulazione", "auto_percentuale": 33,
                 "auto_importo_min": 10, "auto_importo_max": 50, "auto_max_posizioni": 3, "auto_max_trade_giorno": 10})
    cfg = load_config()
    assert plan_horizon(cfg, "1h") == 24
    assert lab.current_id(cfg) == lab_rimbalzo.rimb_id(cfg["segnali_timeframe"], 3.5, 2.0, 24)
    store.add_deposit("simulazione", 100.0, "test")
    f = Fakes()
    eng = f.engine()
    eng.trader.broker_factory = lambda c: PaperBroker("bybiteu", last_price=lambda sym: 100.0)
    close = 1_800_000_000_000 // HOUR * HOUR
    f.final = pd.DataFrame([dip_row("AAA/USDT", True), dip_row("BBB/USDT", False)])
    out = eng.confirm(load_config(), close)
    assert out["signals"] == ["AAA/USDT"]
    assert any("RIMBALZO" in t for t in f.sent)
    pos = store.positions("simulazione")
    assert [p["symbol"] for p in pos] == ["AAA/USDT"] and pos[0]["horizon"] == 24
    assert pos[0]["sell"] > pos[0]["buy"] > pos[0]["stop"]


def test_rimbalzo_live_plan_matches_lab_rule():
    import numpy as np

    from indicators import add_indicators
    from test_lab import _rimb_df
    from webapp import lab_rimbalzo as rb

    btc = rb.btc_regime(_rimb_df(n=340, crash=()))
    df = _rimb_df()
    i = len(df) - 3                                      # la candela del crollo (vedi test_lab)
    p = rb.plan(df.iloc[: i + 1].reset_index(drop=True), btc, 3.0, 2.0)
    assert p["dip_hit"] and p["dip_sell"] > p["dip_buy"] > p["dip_stop"] > 0
    assert p["dip_sell"] == add_indicators(df.iloc[: i + 1])["bb_mid"].iloc[-1]   # SELL alla media a 20
    assert not rb.plan(df.iloc[:i].reset_index(drop=True), btc, 3.0, 2.0)["dip_hit"]   # prima del crollo
    assert not rb.plan(df.iloc[: i + 2].reset_index(drop=True), btc, 3.0, 2.0)["dip_hit"]  # solo la prima candela
    down = btc.copy()
    down[:] = False
    assert not rb.plan(df.iloc[: i + 1].reset_index(drop=True), down, 3.0, 2.0)["dip_hit"]  # BTC in discesa
    assert not rb.plan(df.iloc[:100], btc, 3.0, 2.0)["dip_hit"] and np.isfinite(1)        # troppo poche candele


def test_scan_adds_rimbalzo_fields_and_survives_missing_btc(db):  # noqa: F811
    from webapp import engine

    cfg = {**load_config(), "strategia": "rimbalzo", "segnali_quote": "USDC"}
    radar = pd.DataFrame({"symbol": ["X/USDC", "Y/USDC"]})

    def fetch(sym):
        raise ConnectionError("giù")

    engine.add_rimb_fields(cfg, radar, fetch)
    assert radar["dip_hit"].tolist() == [False, False]
    assert any("BTC" in e["message"] for e in store.errors(limit=5))


def test_lab_never_replaces_rimbalzo_chosen_by_hand(db):  # noqa: F811
    from test_lab import _series
    from webapp import lab

    save_config({**load_config(), "strategia": "rimbalzo", "segnali_timeframe": "1h", "lab_auto_applica": True})
    good = _series([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])
    res = lab.run(load_config(), collect_fn=lambda ex, q, tf, n, fee: good if tf == "1h" else [],
                  collect_dip_fn=lambda *a: {}, collect_rimb_fn=lambda *a: {})
    assert res["verified"] and res["applied"] is None and load_config()["strategia"] == "rimbalzo"
