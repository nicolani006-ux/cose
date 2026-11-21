"""PAC su ETF: calcoli, tasse, ribilanciamento con i versamenti, promemoria, API (prezzi finti: niente rete)."""
from datetime import date

import pandas as pd
import pytest
from starlette.testclient import TestClient

from webapp import pac, server, store


def fake_prices(tickers, start):
    idx = pd.bdate_range("2020-01-01", "2026-11-13")
    base = {"AAA.MI": 100.0, "BBB.MI": 50.0}
    out = {}
    for t in tickers:
        if t in base:  # sale del 50% in modo regolare, con un calo del 30% a metà 2022
            s = pd.Series(base[t] * (1 + 0.5 * pd.RangeIndex(len(idx)) / len(idx)), index=idx)
            s[(s.index >= "2022-06-01") & (s.index < "2022-09-01")] *= 0.7
            out[t] = s
    return out


@pytest.fixture
def db():
    store.DB_FILE.unlink(missing_ok=True)
    store._initialized.clear()
    yield


def plan(**kw):
    return pac.clean_plan({"monthly": 300, "day": 5, "fees": 1,
                           "etfs": [{"ticker": "aaa.mi", "weight": 3}, {"ticker": "BBB.MI", "weight": 1}], **kw})


def test_plan_validation_normalizes_weights():
    p = plan()
    assert [e["ticker"] for e in p["etfs"]] == ["AAA.MI", "BBB.MI"]
    assert [e["weight"] for e in p["etfs"]] == [75.0, 25.0]
    for bad in ({"monthly": 0}, {"day": 31}, {"etfs": []}, {"etfs": [{"ticker": "A A", "weight": 1}]},
                {"etfs": [{"ticker": "X.MI", "weight": 1}, {"ticker": "x.mi", "weight": 1}]},
                {"etfs": [{"ticker": "X.MI", "weight": "abc"}]}, {"monthly": float("nan")}):
        with pytest.raises(pac.PacError):
            plan(**bad)


def test_buy_validation():
    b = pac.clean_buy({"date": "2026-01-05", "ticker": "aaa.mi", "qty": "2", "price": "100", "fees": "1"},
                      today=date(2026, 11, 17))
    assert b["ticker"] == "AAA.MI" and b["qty"] == 2 and len(b["id"]) == 12
    for bad in ({"date": "2027-01-01"}, {"date": "ieri"}, {"qty": 0}, {"price": -1}, {"ticker": "<script>"}):
        with pytest.raises(pac.PacError):
            pac.clean_buy({"date": "2026-01-05", "ticker": "AAA.MI", "qty": 1, "price": 1, **bad},
                          today=date(2026, 11, 17))


def test_summary_values_taxes_and_rebalancing(db):
    st = {"plan": plan(), "buys": [
        {"id": "1", "date": "2021-01-04", "ticker": "AAA.MI", "qty": 10, "price": 100, "fees": 2},
        {"id": "2", "date": "2021-02-01", "ticker": "AAA.MI", "qty": 5, "price": 100, "fees": 0}]}
    s = pac.summary(st, fetch=fake_prices, today=date(2026, 11, 17))
    pos = {p["ticker"]: p for p in s["positions"]}
    last = fake_prices(["AAA.MI"], "")["AAA.MI"].iloc[-1]
    assert pos["AAA.MI"]["qty"] == 15 and pos["AAA.MI"]["cost"] == 1502
    t = s["totals"]
    assert t["value"] == pytest.approx(15 * last)
    assert t["tax_if_sold"] == pytest.approx((15 * last - 1502) * 0.26)
    assert t["stamp_year"] == pytest.approx(15 * last * 0.002)
    assert t["max_drawdown"] >= 29                     # il calo del 30% del 2022 si vede
    # tutto in AAA (peso 75%): il versamento del mese va tutto su BBB, che è sotto il suo 25%
    nx = {r["ticker"]: r for r in s["next"]["rows"]}
    assert nx["BBB.MI"]["eur"] == pytest.approx(300) and nx["AAA.MI"]["eur"] == pytest.approx(0)
    assert s["next"]["status"] == "da fare" and s["curve"] and not s["missing"]
    assert s["backtest"]["runs"] and all(r["value"] > 0 for r in s["backtest"]["runs"])


def test_rebalancing_without_positions_follows_weights(db):
    s = pac.summary({"plan": plan(), "buys": []}, fetch=fake_prices, today=date(2026, 11, 3))
    nx = {r["ticker"]: r["eur"] for r in s["next"]["rows"]}
    assert nx == pytest.approx({"AAA.MI": 225, "BBB.MI": 75})
    assert s["next"]["status"] == "in arrivo" and s["totals"]["value"] == 0


def test_projection_math():
    row = next(r for r in pac.projection(100) if r["years"] == 10)
    assert row["invested"] == 12000
    assert 16500 < row["r7"] < 17500                    # ~17.000 € con il 7% annuo
    assert row["r7_net"] == pytest.approx(row["r7"] - (row["r7"] - 12000) * 0.26)


def test_missing_ticker_and_network_error(db):
    s = pac.summary({"plan": pac.clean_plan({"monthly": 100, "day": 5, "etfs": [{"ticker": "ZZZ.MI", "weight": 1}]}),
                     "buys": []}, fetch=fake_prices, today=date(2026, 11, 17))
    assert s["missing"] == ["ZZZ.MI"]

    def boom(t, s):
        raise ConnectionError("rete giù")

    s = pac.summary({"plan": plan(), "buys": []}, fetch=boom, today=date(2026, 11, 17), force=True)
    assert set(s["missing"]) == {"AAA.MI", "BBB.MI"}
    assert any("pac" in e["source"] for e in store.errors(limit=5))


def test_reminder_once_a_month(db):
    sent = []
    notify = lambda cfg, level, title, text, short: sent.append(title)  # noqa: E731
    assert not pac.reminder(notify, {}, today=date(2026, 11, 20))        # PAC non configurato: silenzio
    pac.save({"plan": plan(), "buys": []})
    assert not pac.reminder(notify, {}, today=date(2026, 11, 4))         # prima del giorno 5
    assert pac.reminder(notify, {}, today=date(2026, 11, 5))
    assert not pac.reminder(notify, {}, today=date(2026, 11, 6))         # una volta sola al mese
    assert pac.reminder(notify, {}, today=date(2026, 12, 9))
    st = pac.load()
    st["buys"].append({"id": "x", "date": "2027-01-02", "ticker": "AAA.MI", "qty": 1, "price": 1, "fees": 0})
    pac.save(st)
    assert not pac.reminder(notify, {}, today=date(2027, 1, 10))         # già versato a gennaio
    assert len(sent) == 2 and "PAC" in sent[0]


def test_pac_api(db, monkeypatch):
    monkeypatch.setattr(pac, "yf_history", fake_prices)
    c = TestClient(server.create_app(), base_url="http://127.0.0.1:8765")
    H = {"X-Radar": "1"}
    assert c.get("/api/pac").json()["plan"]["etfs"][0]["ticker"] == "SWDA.MI"   # piano d'esempio
    assert c.post("/api/pac/plan", json={"monthly": 100}).status_code == 403          # senza X-Radar
    r = c.post("/api/pac/plan", json={"monthly": 0, "day": 5, "etfs": [{"ticker": "AAA.MI", "weight": 1}]}, headers=H)
    assert r.status_code == 400 and "Versamento" in r.json()["error"]
    assert c.post("/api/pac/plan", json={"monthly": 200, "day": 3, "etfs": [{"ticker": "AAA.MI", "weight": 1}]},
                  headers=H).status_code == 200
    b = c.post("/api/pac/buy", json={"date": "2025-03-03", "ticker": "AAA.MI", "qty": 2, "price": 120}, headers=H).json()
    d = c.get("/api/pac").json()
    assert d["totals"]["buys"] == 1 and d["positions"][0]["qty"] == 2 and d["totals"]["value"] > 0
    assert c.post("/api/pac/buy/delete", json={"id": "nope"}, headers=H).status_code == 404
    assert c.post("/api/pac/buy/delete", json={"id": b["buy"]["id"]}, headers=H).status_code == 200
    assert c.get("/api/pac").json()["totals"]["buys"] == 0


def test_closes_from_yfinance_shapes():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    one = pd.DataFrame({("SWDA.MI", "Close"): [130.0, 131.0], ("SWDA.MI", "Open"): [1, 1]}, index=idx)
    one.columns = pd.MultiIndex.from_tuples(one.columns)
    assert pac.closes_from(one, ["SWDA.MI"])["SWDA.MI"].tolist() == [130.0, 131.0]       # un solo ticker
    two = pd.concat({"A.MI": pd.DataFrame({"Close": [1.0, 2.0]}, index=idx),
                     "B.MI": pd.DataFrame({"Close": [3.0, float("nan")]}, index=idx)}, axis=1)
    got = pac.closes_from(two, ["A.MI", "B.MI", "C.MI"])
    assert got["A.MI"].tolist() == [1.0, 2.0] and got["B.MI"].tolist() == [3.0] and "C.MI" not in got
    flat = pd.DataFrame({"Close": [5.0, 6.0]}, index=idx)
    assert pac.closes_from(flat, ["X.MI"])["X.MI"].tolist() == [5.0, 6.0]
