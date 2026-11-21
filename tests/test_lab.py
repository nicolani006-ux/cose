"""Laboratorio delle strategie: portafoglio con le regole del bot, walk-forward e applicazione automatica."""
import pytest
from starlette.testclient import TestClient

from stocks.config import DEFAULT_CONFIG, load_config, save_config
from webapp import lab, server, store

H = 3_600_000
RULES = {"auto_percentuale": 50, "auto_importo_min": 10, "auto_importo_max": 50, "auto_riserva": 0,
         "auto_max_posizioni": 2, "pausa_stesso_simbolo_ore": 6}


def cand(sym, t_h, ret, score=55, prev=45, prob=42, status=None, dur_h=8):
    st = status or ("win" if ret > 0 else "loss")
    r = {"symbol": sym, "t": t_h * H, "score": score, "prev": prev, "prob": prob, "buy": 1, "sell": 2, "stop": 0.5,
         "status": st}
    if st != "unfilled":
        r.update(entry=1.0, exit=1 + ret / 100, t_in=t_h * H + H, t_out=(t_h + dur_h) * H, ret=ret)
    return r


@pytest.fixture
def db():
    store.DB_FILE.unlink(missing_ok=True)
    store._initialized.clear()
    save_config({**DEFAULT_CONFIG, "segnali_exchange": "bybiteu", "segnali_quote": "USDC", "segnali_timeframe": "4h",
                 "soglia_massimo": 50, "probabilita_minima": 40, **RULES})
    yield
    save_config(dict(DEFAULT_CONFIG))


def test_portfolio_follows_bot_rules():
    cands = [cand("A/USDC", 0, 10), cand("A/USDC", 2, 10),            # stessa coin in pausa (6 h): saltata
             cand("B/USDC", 1, -5), cand("C/USDC", 3, 4),             # C: già 2 aperte -> saltata
             cand("D/USDC", 4, 3, prev=55),                           # era già sopra la soglia: niente segnale
             cand("E/USDC", 5, 3, prob=30),                           # probabilità troppo bassa
             cand("F/USDC", 20, 2, status="unfilled")]                # BUY mai eseguito
    pf = portfolio = lab.portfolio(cands, 50, 40, RULES)
    syms = [t["symbol"] for t in pf["trades"]]
    assert syms == ["A/USDC", "B/USDC"]
    a, b = pf["trades"]
    assert a["cost"] == 50 and b["cost"] == 25          # 50% di 100 con tetto 50, poi 50% dei 50 rimasti
    s = portfolio["summary"]
    assert s["trades"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["pnl"] == pytest.approx(50 * 0.10 + 25 * -0.05)
    assert pf["curve"][-1]["v"] == pytest.approx(100 + s["pnl"])


def _series(rets, start_h=0, step_h=24, sym_prefix="S"):
    return [cand(f"{sym_prefix}{i}/USDC", start_h + i * step_h, r) for i, r in enumerate(rets)]


def test_walk_forward_rejects_strategy_that_fails_in_verification(db):
    # ottima nei primi 2/3, pessima nell'ultimo terzo: non va applicata
    cands = _series([5] * 12 + [-5] * 6)
    ev = lab.evaluate({"4h": cands}, RULES)
    assert ev["best"] is not None and not ev["verified"]


def test_run_applies_only_verified_strategy(db):
    good = _series([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])

    def fake_collect(ex, quote, tf, n, fee):
        return good if tf == "1h" else []

    res = lab.run(load_config(), collect_fn=fake_collect, collect_dip_fn=lambda *a: {})
    assert res["verified"] and res["applied"] and res["applied"].startswith("1h|")
    cfg = load_config()
    assert cfg["segnali_timeframe"] == "1h" and res["detail"]["id"] == res["applied"]
    assert any("Laboratorio" in e["title"] for e in store.events(limit=10))

    save_config({**load_config(), "segnali_timeframe": "4h", "lab_auto_applica": False})
    res = lab.run(load_config(), collect_fn=fake_collect, collect_dip_fn=lambda *a: {})
    assert res["applied"] is None and load_config()["segnali_timeframe"] == "4h"


def test_run_applies_verified_dip_strategy(db):
    good = _series([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])
    sid = lab.dip_id("1h", 35, 2, 3, 120)

    def fake_dip(ex, quote, tf, n, fee):
        return {sid: ("1h", (35, 2.0, 3.0, 120), good)} if tf == "1h" else {}

    res = lab.run(load_config(), collect_fn=lambda *a: [], collect_dip_fn=fake_dip)
    assert res["verified"] and res["applied"] == sid == res["current"] == res["detail"]["id"]
    cfg = load_config()
    assert cfg["strategia"] == "dip" and cfg["segnali_timeframe"] == "1h"
    assert (cfg["dip_rsi"], cfg["dip_stop_atr"], cfg["dip_target_atr"], cfg["dip_candele_max"]) == (35, 2.0, 3.0, 120)
    assert lab.current_id(cfg) == sid


def test_dip_plan_and_series():
    import numpy as np
    import pandas as pd

    from indicators import add_indicators, dip_plan, dip_series

    n = 260  # lunga salita, poi tre candele in forte calo: RSI sotto 35 con il prezzo ancora sopra la EMA 200
    close = list(np.linspace(100, 200, n - 3)) + [188, 178, 170]
    df = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"), "open": close,
                       "high": [c * 1.01 for c in close], "low": [c * 0.99 for c in close], "close": close,
                       "volume": [1.0] * n})
    d = add_indicators(df)
    hits = dip_series(df, d, 35)
    assert len(hits) == 1
    i = int(hits[0])
    plan = dip_plan(df.iloc[: i + 1], level=35, k_stop=2, k_target=3)
    assert plan["dip_hit"] and plan["above_ema200"]
    assert plan["dip_sell"] > plan["dip_buy"] > close[i] > plan["dip_stop"]
    assert not dip_plan(df.iloc[:i], level=35)["dip_hit"]   # la candela prima non è ancora il segnale


def test_lab_api(db, monkeypatch):
    monkeypatch.setattr(lab, "start", lambda force=False: True)
    store.kv_set(lab.KV_KEY, {"generated_at": "2026-11-13T10:00:00+00:00", "quote": "USDC", "rows": []})
    c = TestClient(server.create_app(), base_url="http://127.0.0.1:8765")
    d = c.get("/api/lab").json()
    assert d["quote"] == "USDC" and d["refresh_hours"] == lab.REFRESH_HOURS
    assert c.post("/api/lab/run", json={}).status_code == 403               # senza X-Radar
    assert c.post("/api/lab/run", json={}, headers={"X-Radar": "1"}).json()["started"] is True
    c.post("/api/lab/auto", json={"active": False}, headers={"X-Radar": "1"})
    assert load_config()["lab_auto_applica"] is False
    assert "lab" in c.get("/api/overview").json()


def _rimb_df(n=320, crash=(0.95, 0.90, 0.86), freq="h"):
    import numpy as np
    import pandas as pd

    close = list(np.linspace(100, 130, n - len(crash)) * (1 + 0.002 * np.sin(np.arange(n - len(crash)))))
    close += [close[-1] * k for k in crash]
    return pd.DataFrame({"time": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"), "open": close,
                         "high": [c * 1.003 for c in close], "low": [c * 0.997 for c in close], "close": close,
                         "volume": [1.0] * n})


def test_rimbalzo_signal_needs_btc_uptrend():
    import pandas as pd

    from indicators import add_indicators
    from webapp import lab_rimbalzo as rb

    df = _rimb_df()
    d = add_indicators(df)
    up = rb.btc_regime(_rimb_df(crash=()))          # BTC in salita costante: sopra la EMA 200
    hits = rb.signals(df, d, 3.0, up)
    assert len(hits) == 1 and hits[0] == len(df) - 3   # solo la prima candela sotto la banda
    down = pd.Series(False, index=up.index)
    assert len(rb.signals(df, d, 3.0, down)) == 0      # BTC in discesa: niente segnale
    assert len(rb.signals(df, d, 3.0, pd.Series(dtype=bool))) == 0
    assert len(rb.btc_regime(None)) == 0


def test_rimbalzo_collect_builds_bot_compatible_plan():
    from webapp import lab_rimbalzo as rb

    up = _rimb_df(n=340, crash=())
    coin = _rimb_df(n=330, crash=(0.95, 0.90, 0.86, 0.88, 0.92, 0.97, 1.0, 1.0, 1.0, 1.0))
    out = rb.collect("bybiteu", "USDC", "1h", 2, 0.1, fetch=lambda s: up if s.startswith("BTC") else coin,
                     tops=["X/USDC"])
    assert set(out) == {rb.rimb_id("1h", b, k, h) for b in rb.RIMB_BANDS for k in rb.RIMB_STOPS for h in rb.RIMB_HORIZONS}
    rows = [r for (_, _, lst) in out.values() for r in lst]
    assert rows, "il crollo deve dare almeno un candidato"
    for r in rows:
        assert r["symbol"] == "X/USDC" and r["sell"] > r["buy"] > r["stop"] > 0
        if r["status"] == "win":
            assert r["ret"] > 0
    assert rb.collect("bybiteu", "USDC", "4h", 2, 0.1, fetch=lambda s: up, tops=["X/USDC"]) == {}


def test_rimbalzo_is_never_applied_even_if_best(db):
    good = _series([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])
    rid = "rimb|1h|3|1.5|80"
    before = load_config()

    def fake_rimb(ex, quote, tf, n, fee):
        return {rid: ("1h", (3.0, 1.5, 80), good)}

    res = lab.run(load_config(), collect_fn=lambda *a: [], collect_dip_fn=lambda *a: {}, collect_rimb_fn=fake_rimb)
    assert res["applied"] is None and res["best"] is None and not res["verified"]
    assert res["watch"]["id"] == rid and res["watch"]["verified"] and res["watch"]["trades"]
    after = load_config()
    assert after.get("strategia") == before.get("strategia") and after["segnali_timeframe"] == before["segnali_timeframe"]
    row = next(r for r in res["rows"] if r["id"] == rid)
    assert row["kind"] == "rimbalzo" and row["research"] and not row["chosen"]


def test_rimbalzo_failure_does_not_stop_lab(db):
    def boom(*a):
        raise RuntimeError("rete giù")

    good = _series([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])
    res = lab.run(load_config(), collect_fn=lambda ex, q, tf, n, fee: good if tf == "1h" else [],
                  collect_dip_fn=lambda *a: {}, collect_rimb_fn=boom)
    assert res["verified"] and res["watch"] is None
