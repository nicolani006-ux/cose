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
        monkeypatch.setattr(ind, "score_series", lambda d: signal)  # un solo segnale, alla candela 70
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
