import numpy as np
import pandas as pd
import pytest

import fake_stocks as fk
import indicators as ind
from charts import price_chart


def test_score_series_equals_analyze_on_every_cut():
    for seed in range(6):
        df = fk.prices(0.001 * (seed - 3), n=320, seed=seed)
        df.loc[df.index[::13], "volume"] *= 3  # picchi di volume
        ss = ind.score_series(df)
        for cut in (60, 120, 198, 199, 250, 319):
            assert ind.analyze(df.iloc[:cut + 1])["score"] == pytest.approx(round(ss.iloc[cut], 1))


def test_score_series_uses_no_future_data():
    df = fk.prices(0.001, n=300, seed=1)
    before = ind.score_series(df).iloc[:250]
    changed = df.copy()
    changed.loc[changed.index[250:], ["open", "high", "low", "close"]] *= 0.5
    pd.testing.assert_series_equal(before, ind.score_series(changed).iloc[:250])


def test_trade_plan_levels():
    df = fk.prices(0.001, n=300, seed=2)
    p = ind.trade_plan(df)
    close = df["close"].iloc[-1]
    assert p["stop"] < p["buy"] < close < p["sell"]
    assert (p["sell"] - p["buy"]) / (p["buy"] - p["stop"]) == pytest.approx(2)
    assert p["upside_pct"] > 0 and p["risk_pct"] > 0
    assert np.isnan(ind.trade_plan(fk.prices(0.001, n=5))["buy"])  # storico troppo corto


def _df(closes, spread=0.004):
    closes = np.asarray(closes, float)
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(closes), freq="D"),
        "open": np.r_[closes[0], closes[:-1]], "high": closes * (1 + spread), "low": closes * (1 - spread),
        "close": closes, "volume": np.full(len(closes), 1e6),
    })


def test_backtest_on_trend_and_random_walk():
    up = ind.backtest_plan(fk.prices(0.004, n=400, seed=0))
    assert up["bt_trades"] >= 1 and up["bt_win_rate"] == 100 and up["bt_avg_ret"] > 0
    rw = ind.backtest_plan(fk.prices(0.0, n=400, seed=1))
    assert rw["bt_losses"] >= 1 and rw["bt_avg_ret"] < 0
    for r in (up, rw):
        assert r["bt_trades"] == r["bt_wins"] + r["bt_losses"] + r["bt_timeouts"] <= r["bt_signals"]
        assert 0 <= r["bt_fill_rate"] <= 100


def test_backtest_stop_and_target_logic(monkeypatch):
    def run(after, horizon=20):
        closes = np.r_[np.full(71, 100.0), after]
        df = _df(closes, spread=0.005)
        signal = pd.Series(np.where(np.arange(len(df)) == 70, 50.0, 0.0), index=df.index)
        monkeypatch.setattr(ind, "score_series", lambda df, d=None: signal)  # un solo segnale, alla candela 70
        return ind.backtest_plan(df, horizon=horizon)

    # ATR ~1: BUY ~99.5 (eseguito alla candela 71), STOP ~98, SELL ~102.5
    win = run([99.4, 101, 102, 103, 104] + [104] * 30)
    assert (win["bt_trades"], win["bt_wins"]) == (1, 1) and win["bt_avg_ret"] == pytest.approx(3.0, abs=0.6)
    loss = run([99.4, 99, 98, 96, 95] + [95] * 30)
    assert (loss["bt_trades"], loss["bt_losses"]) == (1, 1) and loss["bt_avg_ret"] < -1
    flat = run([99.4] + [100.0] * 30, horizon=10)
    assert (flat["bt_trades"], flat["bt_timeouts"]) == (1, 1)
    never = run([101, 102, 103, 104, 105, 106] + [106] * 30)  # nessun ritracciamento: ordine non eseguito
    assert (never["bt_signals"], never["bt_trades"]) == (1, 0) and never["bt_fill_rate"] == 0


def test_overall_stats_weights_by_trades():
    rows = pd.DataFrame({"bt_trades": [10, 0, 30], "bt_wins": [5, 0, 15], "bt_avg_ret": [2.0, np.nan, -2.0]})
    s = ind.overall_stats(rows)
    assert s == {"trades": 40, "win_rate": 50.0, "avg_ret": pytest.approx(-1.0)}
    assert ind.overall_stats(rows.iloc[1:2]) is None


def test_price_chart_draws_levels():
    df = ind.add_indicators(fk.prices(0.001, n=200))
    chart = price_chart(df, "X", {"BUY": 100.0, "SELL": 110.0, "STOP": np.nan})
    assert len(chart.layer) == 5
    assert len(price_chart(df, "X").layer) == 3


def test_stat_text():
    assert "nessun segnale" in ind.stat_text({"bt_trades": 0})
    txt = ind.stat_text({"bt_trades": 12, "bt_win_rate": 41.7, "bt_avg_ret": 1.23, "bt_avg_bars": 9.4}, "giorni")
    assert txt.startswith("42% dei 12 acquisti") and "+1.2%" in txt and "9 giorni" in txt


def test_position_size_like_trader_budget():
    # esempio della skill: conto 5000$, perdita max 50$, TSLA BUY 364.18 STOP 358.32 -> 8 azioni
    sz = ind.position_size(364.18, 358.32, 370.04, capital=5000, max_loss=50)
    assert sz["qty"] == 8 and sz["bound_by"] == "perdita massima allo STOP"
    assert sz["cost"] == pytest.approx(2913.44) and sz["loss_at_stop"] == pytest.approx(46.88)
    # capitale piccolo: decide la liquidità (90% di 1000$ / 364.18 = 2 azioni)
    assert ind.position_size(364.18, 358.32, 370.04, 1000, 50)["qty"] == 2
    # crypto: frazioni
    sz = ind.position_size(100.0, 98.0, 106.0, 1000, 10, fractional=True)
    assert sz["qty"] == pytest.approx(5.0) and sz["gain_at_target"] == pytest.approx(30.0)
    # STOP troppo lontano per la perdita massima: rifiuto chiaro, non un ordine più piccolo
    sz = ind.position_size(100.0, 40.0, 220.0, 10000, 50)
    assert sz["qty"] == 0 and "troppo lontano" in sz["why"]
    assert ind.position_size(100, 98, 106, 0, 50) is None
    assert "Quantità 8" in ind.sizing_text(ind.position_size(364.18, 358.32, 370.04, 5000, 50))


def test_monte_carlo_plan_is_reproducible_and_consistent():
    df = fk.prices(0.001, n=400, seed=5)
    p = ind.trade_plan(df)
    a = ind.monte_carlo_plan(df, p["buy"], p["stop"], p["sell"], 20)
    b = ind.monte_carlo_plan(df, p["buy"], p["stop"], p["sell"], 20)
    assert a == b and a["mc_replays"] == 2000                      # stesso seme: stessi risultati
    assert a["mc_win"] + a["mc_loss"] + a["mc_timeout"] == pytest.approx(100)
    assert 0 < a["mc_fill_rate"] <= 100 and a["mc_break_even"] == pytest.approx(100 / 3)
    assert ind.monte_carlo_plan(df, 10, 11, 12, 20) == {"mc_replays": 0}   # piano non valido
    assert ind.mc_verdict(50, 100 / 3).startswith("Fortemente verso il SELL")
    assert ind.mc_verdict(36, 100 / 3).startswith("Vicino al pareggio")
    assert ind.mc_verdict(25, 100 / 3).startswith("Tende verso lo STOP")


def test_monte_carlo_matches_simulate_trade_rules():
    """Una sola candela possibile: il risultato Monte Carlo deve coincidere con simulate_trade."""
    n = 120
    closes = 100 * 1.001 ** np.arange(n)
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
                       "open": closes / 1.001, "high": closes * 1.01, "low": closes * 0.995,
                       "close": closes, "volume": 1.0})
    last = closes[-1]
    buy, stop, target = last * 0.999, last * 0.98, last * 1.03
    mc = ind.monte_carlo_plan(df, buy, stop, target, horizon=20, replays=50)
    assert mc["mc_fill_rate"] == 100 and mc["mc_win"] == 100            # sale sempre: SELL ogni volta
