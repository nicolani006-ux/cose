"""Sicurezza dei soldi veri (dalla revisione del codice): nessun ordine perso, STOP sempre attivo, SELL sempre
presente sull'exchange, posizioni reali controllate anche in simulazione."""
import ccxt
import pytest

from stocks.config import load_config, save_config
from test_autotrade import CLOSE, HOUR, FakeExchange, env, sig  # noqa: F401 - env è una fixture
from test_bybit_live import FakeBybit
from webapp import autotrade, lab, store


class Fx(FakeExchange):
    """Exchange finto con id cliente sugli ordini e ricerca degli ordini."""

    def __init__(self):
        super().__init__()
        self.fail_next_buy = None

    def create_limit_buy_order(self, sym, qty, price, params=None):
        o = self._order("buy", sym, qty, price)
        o["clientOrderId"] = (params or {}).get("clientOrderId")
        if self.fail_next_buy:  # l'ordine arriva all'exchange ma la risposta si perde
            e, self.fail_next_buy = self.fail_next_buy, None
            raise e
        return o

    def fetch_open_orders(self, sym):
        return [o for o in self.orders.values() if o["status"] == "open"]

    def fetch_closed_orders(self, sym):
        return [o for o in self.orders.values() if o["status"] != "open"]


def live_trader(fx, sent):
    return autotrade.AutoTrader(lambda *a, **k: sent.append(a[2]),
                                broker_factory=lambda cfg: autotrade.LiveBroker(fx, sleep=lambda s: None))


def test_lost_buy_response_is_recovered(env):  # noqa: F811
    _, _, sent = env
    fx = Fx()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = live_trader(fx, sent)
    fx.fail_next_buy = ccxt.RequestTimeout("timeout")
    trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE)
    p = store.positions("reale")[0]
    assert p["status"] == "ordine" and not p.get("buy_order") and p["client_id"].startswith("radar-")
    trader.tick(load_config(), CLOSE + 30_000)            # ritrovato per id cliente
    p = store.positions("reale")[0]
    assert p["buy_order"] == "o1"
    fx.orders["o1"].update(status="closed", filled=4.04, average=99.0, cost=399.96)
    fx.free["AAA"] = 4.036
    trader.tick(load_config(), CLOSE + 60_000)
    assert store.positions("reale")[0]["status"] == "aperta"


def test_buy_never_reached_exchange_is_closed_after_expiry(env):  # noqa: F811
    _, _, sent = env
    fx = Fx()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = live_trader(fx, sent)
    fx.create_limit_buy_order = lambda *a, **k: (_ for _ in ()).throw(ccxt.NetworkError("rete giù"))
    trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE)
    p = store.positions("reale")[0]
    trader.tick(load_config(), p["buy_expires"] + autotrade.LOST_ORDER_GRACE_MS + 1)
    p = store.positions("reale")[0]
    assert p["status"] == "annullata" and "non arrivato" in p["reason"]


def test_expired_buy_filled_during_cancel_is_kept(env):  # noqa: F811
    _, _, sent = env
    fx = Fx()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = live_trader(fx, sent)
    trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE)
    p = store.positions("reale")[0]

    def cancel(oid, sym):   # si esegue proprio mentre lo annulliamo
        fx.orders[oid].update(status="closed", filled=4.04, average=99.0, cost=399.96)
        fx.free["AAA"] = 4.036
        raise ccxt.OrderNotFound(oid)

    fx.cancel_order = cancel
    trader.tick(load_config(), p["buy_expires"] + 1)
    assert store.positions("reale")[0]["status"] == "aperta"


def test_sell_order_is_replaced_if_missing_or_cancelled(env):  # noqa: F811
    _, _, sent = env
    fx = Fx()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = live_trader(fx, sent)
    trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE)
    fx.orders["o1"].update(status="closed", filled=4.04, average=99.0, cost=399.96)
    fx.free["AAA"] = 4.036
    trader.tick(load_config(), CLOSE + 60_000)
    p = store.positions("reale")[0]
    fx.orders[p["sell_order"]]["status"] = "canceled"     # annullato a mano sull'exchange
    trader.tick(load_config(), CLOSE + 120_000)
    p2 = store.positions("reale")[0]
    assert p2["sell_order"] != p["sell_order"] and fx.orders[p2["sell_order"]]["status"] == "open"


def test_stop_never_closes_without_selling():
    ex = FakeBybit()
    ex.orders["s1"] = {"status": "open", "filled": 0.0, "average": None, "cost": 0.0}
    ex.balance["SOL"] = 0.0   # l'exchange non ha ancora liberato le coin del SELL annullato
    p = {"symbol": "SOL/USDC", "qty": 0.5, "sell_order": "s1", "sell": 110.0}
    with pytest.raises(RuntimeError):
        autotrade.LiveBroker(ex, sleep=lambda s: None).sell_market(p, 90.0)
    assert ex.market_sells == []


def test_real_positions_checked_while_in_simulation(env):  # noqa: F811
    _, prices, sent = env
    fx = Fx()
    save_config({**load_config(), "auto_modalita": "reale"})
    trader = autotrade.AutoTrader(lambda *a, **k: sent.append(a[2]),
                                  broker_factory=lambda cfg: autotrade.LiveBroker(fx, sleep=lambda s: None)
                                  if cfg.get("auto_modalita") == "reale" else autotrade.PaperBroker("binance", prices))
    trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE)
    fx.orders["o1"].update(status="closed", filled=4.04, average=99.0, cost=399.96)
    fx.free["AAA"] = 4.036
    trader.tick(load_config(), CLOSE + 60_000)
    save_config({**load_config(), "auto_modalita": "simulazione"})   # passato alla simulazione
    fx.last = 96.0                                                    # sotto lo STOP
    trader.tick(load_config(), CLOSE + 120_000)
    p = store.positions("reale")[0]
    assert p["status"] == "chiusa" and p["reason"] == "STOP"


def test_old_signal_is_not_bought(env):  # noqa: F811
    trader, prices, _ = env
    store.add_deposit("simulazione", 100.0, "test")
    prices.p["AAA/USDT"] = 100.0
    out = trader.on_signals(load_config(), [sig("AAA/USDT")], CLOSE, now_ms=CLOSE + HOUR)
    assert out == [] and store.positions("simulazione") == []


def test_lab_never_switches_strategy_with_real_money(env):  # noqa: F811
    good = [{"symbol": f"S{i}/USDC", "t": i * 24 * HOUR, "score": 55, "prev": 45, "prob": 42, "buy": 1, "sell": 2,
             "stop": 0.5, "status": "win" if r > 0 else "loss", "entry": 1.0, "exit": 1 + r / 100,
             "t_in": i * 24 * HOUR + HOUR, "t_out": i * 24 * HOUR + 8 * HOUR, "ret": r}
            for i, r in enumerate([4, -2, 4, 4, -2, 4, 4, 4, -2, 4, 4, 4, 4, -2, 4, 4, 4, 4])]
    save_config({**load_config(), "auto_modalita": "reale", "segnali_timeframe": "4h", "lab_auto_applica": True})
    res = lab.run(load_config(), collect_fn=lambda ex, q, tf, n, fee: good if tf == "1h" else [],
                  collect_dip_fn=lambda *a: {})
    assert res["verified"] and res["applied"] is None and load_config()["segnali_timeframe"] == "4h"
