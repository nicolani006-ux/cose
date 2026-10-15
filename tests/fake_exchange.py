"""Exchange finto e deterministico per testare l'app senza rete."""

import numpy as np

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "USDC", "BTCUP"]


class FakeExchange:
    def __init__(self, *_args, **_kwargs):
        self.markets = {}

    def load_markets(self):
        self.markets = {
            f"{c}/USDT": {"spot": True, "active": True, "base": c, "quote": "USDT"} for c in COINS
        }
        return self.markets

    def fetch_tickers(self):
        return {
            s: {"last": 100.0 + i, "percentage": (-1) ** i * i, "quoteVolume": 1e9 / (i + 1)}
            for i, s in enumerate(self.markets)
        }

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=300):
        seed = sum(map(ord, symbol))
        rng = np.random.default_rng(seed)
        drift = 0.002 if seed % 2 else -0.002
        close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, limit)))
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * 1.003
        low = np.minimum(open_, close) * 0.997
        vol = rng.uniform(100, 200, limit)
        t0 = 1_700_000_000_000
        return [[t0 + i * 3_600_000, open_[i], high[i], low[i], close[i], vol[i]] for i in range(limit)]
