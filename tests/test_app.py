import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import trend_crypto as tc  # noqa: E402
from fake_exchange import FakeExchange  # noqa: E402


def make_df(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC"),
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(len(closes), 100.0),
    })


def test_rsi_bounds_and_extremes():
    up = tc.rsi(pd.Series(np.arange(1, 101, dtype=float)))
    assert up.iloc[-1] == pytest.approx(100)
    down = tc.rsi(pd.Series(np.arange(100, 0, -1, dtype=float)))
    assert down.iloc[-1] == pytest.approx(0)
    rand = tc.rsi(pd.Series(np.random.default_rng(1).normal(0, 1, 500).cumsum() + 100)).dropna()
    assert rand.between(0, 100).all()


def test_ema_matches_pandas():
    s = pd.Series(np.linspace(1, 10, 50))
    assert tc.ema(s, 10).iloc[-1] == pytest.approx(s.ewm(span=10, adjust=False).mean().iloc[-1])


def test_uptrend_scores_positive_downtrend_negative():
    up = tc.analyze(make_df(100 * 1.01 ** np.arange(250) + np.sin(np.arange(250))))
    down = tc.analyze(make_df(100 * 0.99 ** np.arange(250) + np.sin(np.arange(250))))
    assert up["score"] > 20 and "RIALZO" in up["signal"]
    assert down["score"] < -20 and "RIBASSO" in down["signal"]
    assert -100 <= down["score"] <= up["score"] <= 100


def test_crossed_detects_direction():
    a = pd.Series([1, 1, 1, 3, 3], dtype=float)
    b = pd.Series([2, 2, 2, 2, 2], dtype=float)
    assert tc._crossed(a, b, 3) == 1
    assert tc._crossed(b, a, 3) == -1
    assert tc._crossed(a, b, 1) == 0


def test_stablecoins_and_leveraged_tokens_filtered():
    assert tc.is_tradeable_base("BTC")
    assert tc.is_tradeable_base("UP")  # il token "UP" in sé non è a leva
    assert not tc.is_tradeable_base("USDC")
    assert not tc.is_tradeable_base("BTCUP")
    assert not tc.is_tradeable_base("ETH3L")


def test_excel_export_roundtrip(monkeypatch):
    monkeypatch.setattr(tc.ccxt, "binance", FakeExchange, raising=False)
    radar, errors = tc.scan("binance", "USDT", 10, "1h")
    assert not errors
    data = tc.to_excel(radar)
    back = pd.read_excel(__import__("io").BytesIO(data))
    assert len(back) == len(radar) == 6  # USDC e BTCUP esclusi
    assert "Punteggio" in back.columns


def test_full_app_runs_without_errors(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(tc, "fear_greed", lambda: {"value": 55, "label": "Greed", "prev": 50})
    monkeypatch.setattr(tc, "fetch_news", lambda: pd.DataFrame(
        [{"source": "Test", "title": "Ciao", "link": "https://example.com",
          "published": pd.Timestamp("2024-01-01", tz="UTC")}]))

    def run():
        import ccxt
        import sys as _sys
        sys_path = str(__import__("pathlib").Path.cwd() / "tests")
        _sys.path.insert(0, sys_path)
        from fake_exchange import FakeExchange as FE
        ccxt.binance = FE
        import trend_crypto
        trend_crypto.main()

    at = AppTest.from_function(run, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert at.title[0].value.endswith("Crypto Radar Ultimate")
    assert len(at.dataframe) == 1 and len(at.dataframe[0].value) == 6
    assert len(at.tabs) == 4
