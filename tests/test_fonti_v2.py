"""Fonti dati più robuste: nuovi tentativi HTTP e riserva per i prezzi quando yfinance non funziona."""

import pandas as pd
import pytest
import requests

from stocks import sources


class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload, self.headers = status, payload, {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class Session:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_http_get_retries_on_429_and_network_errors(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    s = Session([Resp(429), requests.ConnectionError("giù"), Resp(200, {"ok": 1})])
    assert sources.http_get("https://x", session=s).json() == {"ok": 1} and s.calls == 3


def test_http_get_gives_up_and_does_not_retry_404(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        sources.http_get("https://x", session=Session([Resp(503), Resp(503), Resp(503)]))
    s = Session([Resp(404)])
    with pytest.raises(requests.HTTPError):
        sources.http_get("https://x", session=s)
    assert s.calls == 1


CHART = {"chart": {"result": [{
    "timestamp": [1793750400, 1793836800],
    "indicators": {"quote": [{"open": [10.0, 11.0], "high": [12.0, 12.5], "low": [9.5, 10.5], "close": [11.0, 12.0],
                              "volume": [100, 200]}],
                   "adjclose": [{"adjclose": [5.5, 6.0]}]}}]}}


def test_yahoo_chart_adjusts_prices(monkeypatch):
    monkeypatch.setattr(sources, "http_get", lambda url, **k: Resp(200, CHART))
    df = sources.yahoo_chart("AAPL")
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [5.5, 6.0] and df["open"].iloc[0] == 5.0  # rettificati con adjclose/close


def test_fetch_prices_falls_back_when_yfinance_breaks(monkeypatch):
    import yfinance as yf

    class Broken:
        def __init__(self, t):
            pass

        def history(self, **k):
            raise ValueError("Yahoo ha cambiato formato")

    monkeypatch.setattr(yf, "Ticker", Broken)
    monkeypatch.setattr(sources, "http_get", lambda url, **k: Resp(200, CHART))
    out = sources.fetch_prices(["AAPL"])
    assert isinstance(out["AAPL"], pd.DataFrame) and len(out["AAPL"]) == 2
