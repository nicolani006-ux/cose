"""LiveBroker con un finto Bybit (conto Unified): fetch_order richiede acknowledged, lo STOP che scatta
mentre il SELL limite si esegue non deve vendere coin che non ci sono più."""
import ccxt
import pytest

from webapp.autotrade import LiveBroker


class FakeBybit:
    id = "bybiteu"
    has = {"fetchClosedOrder": True}

    def __init__(self):
        self.orders = {}
        self.balance = {"USDC": 20.0}
        self.market_sells = []

    def market(self, symbol):
        return {"base": symbol.split("/")[0], "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}}}

    def amount_to_precision(self, symbol, qty):
        if qty < 0.001:
            raise ccxt.InvalidOrder("amount too small")
        return f"{int(qty * 1000) / 1000:.3f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"

    def fetch_balance(self):
        return {"free": dict(self.balance)}

    def fetch_order(self, oid, symbol, params=None):
        if not (params or {}).get("acknowledged"):
            raise ccxt.ArgumentsRequired("bybiteu fetchOrder() can only access an order if it is in last 500 orders")
        if oid not in self.orders:
            raise ccxt.OrderNotFound(oid)
        return self.orders[oid]

    def fetch_closed_order(self, oid, symbol):
        raise ccxt.OrderNotFound(oid)

    def cancel_order(self, oid, symbol):
        if self.orders.get(oid, {}).get("status") != "open":
            raise ccxt.OrderNotFound(oid)
        self.orders[oid]["status"] = "canceled"

    def create_market_sell_order(self, symbol, qty):
        base = symbol.split("/")[0]
        if self.balance.get(base, 0) < qty:
            raise ccxt.InsufficientFunds("not enough " + base)
        self.balance[base] -= qty
        self.market_sells.append(qty)
        return {"cost": qty * 90.0, "average": 90.0}


def test_buy_status_uses_acknowledged_on_bybit():
    ex = FakeBybit()
    ex.orders["b1"] = {"status": "closed", "filled": 0.1, "average": 100.0, "cost": 10.0}
    ex.balance["SOL"] = 0.0999  # commissione trattenuta sulla coin
    st = LiveBroker(ex).buy_status({"buy_order": "b1", "symbol": "SOL/USDC", "buy": 100.0})
    assert st["done"] and st["filled"] == pytest.approx(0.099) and st["avg"] == 100.0


def test_stop_after_sell_already_filled_does_not_resell():
    ex = FakeBybit()
    ex.orders["s1"] = {"status": "closed", "filled": 0.099, "average": 110.0, "cost": 10.89}
    ex.balance["SOL"] = 0.0
    p = {"symbol": "SOL/USDC", "qty": 0.099, "sell_order": "s1", "sell": 110.0}
    res = LiveBroker(ex).sell_market(p, 90.0)
    assert ex.market_sells == []
    assert res["avg"] == pytest.approx(110.0) and res["proceeds"] == pytest.approx(10.89 * 0.999)


def test_stop_with_partial_sell_sells_only_the_rest():
    ex = FakeBybit()
    ex.orders["s1"] = {"status": "open", "filled": 0.05, "average": 110.0, "cost": 5.5}
    ex.balance["SOL"] = 0.049
    p = {"symbol": "SOL/USDC", "qty": 0.099, "sell_order": "s1", "sell": 110.0}
    res = LiveBroker(ex).sell_market(p, 90.0)
    assert ex.orders["s1"]["status"] == "canceled"
    assert ex.market_sells == [pytest.approx(0.049)]
    assert res["proceeds"] == pytest.approx((5.5 + 0.049 * 90.0) * 0.999)


def test_sell_status_without_cost_is_not_a_total_loss():
    ex = FakeBybit()
    ex.orders["s1"] = {"status": "closed", "filled": 0.099, "average": 110.0, "cost": None}
    p = {"symbol": "SOL/USDC", "qty": 0.099, "sell_order": "s1", "sell": 110.0}
    st = LiveBroker(ex).sell_status(p, 111.0)
    assert st["done"] and st["proceeds"] == pytest.approx(0.099 * 110.0 * 0.999)
