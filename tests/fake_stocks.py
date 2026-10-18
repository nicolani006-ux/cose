"""Dati finti per testare Stock Radar senza rete."""

import numpy as np
import pandas as pd

from stocks.monitor import Snapshot

NOW = pd.Timestamp.now(tz="UTC")


def prices(trend: float, n: int = 260, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(trend + rng.normal(0, 0.008, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "time": pd.date_range(end=NOW.normalize(), periods=n, freq="D"),
        "open": open_, "high": np.maximum(open_, close) * 1.004, "low": np.minimum(open_, close) * 0.996,
        "close": close, "volume": rng.uniform(1e6, 2e6, n),
    })


def ratings(rows: list[tuple]) -> pd.DataFrame:
    cols = ["ticker", "date", "firm", "bank", "weight", "action", "from_grade", "to_grade", "pt_now", "pt_prev"]
    return pd.DataFrame(rows, columns=cols)


def news_items(extra: list[dict] | None = None) -> list[dict]:
    base = [
        {"id": "fed1", "group": "Fed – comunicati", "kind": "banca_centrale", "publisher": "Fed",
         "title": "Federal Reserve issues FOMC statement", "summary": "The Committee decided to lower the target range; inflation easing.",
         "link": "https://fed/1", "published": NOW - pd.Timedelta(hours=2)},
        {"id": "gn1", "group": "Goldman Sachs", "kind": "notizie", "publisher": "Reuters",
         "title": "Goldman Sachs upgrades Nvidia, says AI demand strong", "summary": "",
         "link": "https://n/1", "published": NOW - pd.Timedelta(hours=1)},
        {"id": "gn2", "group": "Wall Street", "kind": "notizie", "publisher": "Bloomberg",
         "title": "Pfizer shares plunge after trial miss", "summary": "",
         "link": "https://n/2", "published": NOW - pd.Timedelta(hours=3)},
    ]
    return base + (extra or [])


def snapshot(rating_rows=None, extra_news=None, holdings=None, filings=None) -> Snapshot:
    from stocks.analysis import enrich_news

    wl = ["NVDA", "PFE", "AAPL", "JPM"]
    news = pd.DataFrame(news_items(extra_news))
    return Snapshot(
        fetched_at=pd.Timestamp.now(tz="UTC"),
        prices={"NVDA": prices(0.003, seed=1), "PFE": prices(-0.003, seed=2),
                "AAPL": prices(0.0005, seed=3), "JPM": prices(0.001, seed=4)},
        ratings=ratings(rating_rows or []),
        news=enrich_news(news, wl),
        filings=filings if filings is not None else pd.DataFrame(),
        holdings=holdings if holdings is not None else pd.DataFrame(),
    )
