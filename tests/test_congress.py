import datetime as dt
from pathlib import Path

import pandas as pd

import fake_stocks as fk
from stocks import analysis, congress, monitor

FIX = Path(__file__).parent / "day_trader" / "fixtures" / "trader-pelosi"
NUL = "\x00"

# Testo come lo restituisce pypdf (casi visti su moduli reali del 2026): attività su più righe,
# transazione intera su una riga, importo spezzato, ticker dopo la transazione a pagina nuova,
# etichette in maiuscoletto con \x00 e resti dei titoli ("P T R").
PYPDF_TEXT = f"""P T R
Name: Hon. Test Member
Status: Member
State/District: CA11
ID Owner Asset Transaction
Type
Gains >
$200?
SP Intel Corporation - Common Stock
(INTC) [OP]
P 05/29/2026 05/29/2026 $1,000,001 -
$5,000,000
F{NUL*5} S{NUL*5}: New
D{NUL*10}: Purchased 200 call options with a strike price of $50 and an expiration date of 3/19/27.
JT Procter & Gamble Company (PG) [ST] S 10/23/2026 11/01/2026 $1,001 - $15,000
F{NUL*5} S{NUL*5}: New
JT Republic Services, Inc. Common P 08/06/2026 09/14/2026 $1,001 - $15,000
Filing ID #20035455
ID Owner Asset Transaction
Stock (RSG) [ST]
F{NUL*5} S{NUL*5}: New
S{NUL*9} O{NUL}: Morgan Stanley - Select UMA Account # 1
"""


def test_pypdf_text_is_rebuilt_for_the_skill_parser():
    rows = congress.ptr().parse_ptr_text(congress.pypdf_text_to_layout(PYPDF_TEXT), "2026-11-05")["rows"]
    got = [(r["owner"], r["ticker"], r["type"], r["date"], r["amount"]) for r in rows]
    assert got == [
        ("SP", "INTC", "P", "2026-05-29", "$1,000,001 - $5,000,000"),
        ("JT", "PG", "S", "2026-10-23", "$1,001 - $15,000"),
        ("JT", "RSG", "P", "2026-08-06", "$1,001 - $15,000"),
    ]
    assert rows[0]["status"] == "New" and "call options" in rows[0]["description"]
    assert "P T R" not in rows[0]["asset"]


def test_recent_filings_from_the_house_index_fixture():
    P = congress.ptr()
    index = P.parse_index(P.read_index_bytes((FIX / "2026FD.zip").read_bytes()), 2026)
    rows = congress.recent_filings(days=60, today=dt.date(2026, 10, 20), index_rows=index)
    assert rows and all(r["type"] == "P" for r in rows)
    assert all("2026-08-21" <= r["filed"] <= "2026-10-20" for r in rows)
    assert rows == sorted(rows, key=lambda r: (r["filed"], r["doc_id"]), reverse=True)
    pelosi = congress.recent_filings(days=365, members=["pelosi"], today=dt.date(2026, 10, 20), index_rows=index)
    assert pelosi and all("Pelosi" in r["name"] for r in pelosi)


def test_filing_trades_rejects_bad_doc_id(tmp_path):
    try:
        congress.filing_trades({"doc_id": "../../etc", "year": 2026}, tmp_path)
    except ValueError as e:
        assert "DocID" in str(e)
    else:
        raise AssertionError("DocID non valido accettato")


def trades():
    return pd.DataFrame([
        {"member": "Nancy Pelosi", "district": "CA11", "filed": "2026-11-05", "doc_id": "1", "url": "u1",
         "owner": "SP", "ticker": "NVDA", "asset": "NVIDIA", "type": "P", "type_name": "purchase",
         "date": "2026-10-23", "amount": "$1,000,001 - $5,000,000", "amount_low": 1_000_001,
         "amount_high": 5_000_000, "days_to_disclose": 13, "late": False},
        {"member": "Josh Gottheimer", "district": "NJ05", "filed": "2026-11-05", "doc_id": "2", "url": "u2",
         "owner": "JT", "ticker": "PFE", "asset": "Pfizer", "type": "S", "type_name": "sale",
         "date": "2026-10-20", "amount": "$1,001 - $15,000", "amount_low": 1001, "amount_high": 15000,
         "days_to_disclose": 16, "late": False},
    ], columns=congress.CONGRESS_COLUMNS)


def test_congress_score_and_text():
    pts, why = analysis.congress_score(trades(), "NVDA")
    assert pts == 25 and why == "Congresso USA: compra Nancy Pelosi"
    assert analysis.congress_score(trades(), "PFE")[0] == -25
    assert analysis.congress_score(trades(), "AAPL") == (0.0, "")
    assert congress.describe_trade(trades().iloc[0].to_dict()).startswith("Nancy Pelosi (coniuge) compra NVDA")


def test_congress_feeds_ranking_and_alerts(monkeypatch):
    monkeypatch.setattr(monitor, "notify_all", lambda *a: ["ok"])
    monitor.STATE_FILE.unlink(missing_ok=True)
    snap = fk.snapshot()
    snap.congress = trades()
    ranking = analysis.score_stocks(snap, ["NVDA", "PFE", "AAPL", "JPM"]).set_index("ticker")
    assert ranking.loc["NVDA", "s_congresso"] == 25 and "Congresso USA: compra Nancy Pelosi" in ranking.loc["NVDA", "reasons"]
    alerts = monitor.build_alerts(snap, ranking.reset_index(), {})
    cong = [a for a in alerts if a["key"].startswith("congress:")]
    assert len(cong) == 2 and cong[0]["level"] == "alta" and "Nancy Pelosi" in cong[0]["text"]


def test_collect_trades_skips_unreadable_filings(monkeypatch, tmp_path):
    monkeypatch.setattr(congress, "recent_filings", lambda days, today=None: [
        {"doc_id": "1", "name": "Hon. A", "district": "CA1", "filed": "2026-11-01", "year": 2026},
        {"doc_id": "2", "name": "Hon. B", "district": "CA2", "filed": "2026-11-02", "year": 2026}])

    def fake(row, cache_dir):
        if row["doc_id"] == "2":
            raise ValueError("pdf rotto")
        return {"url": "u", "rows": [{"ticker": "AAPL", "type": "P", "amount": "$1,001 - $15,000"}]}

    monkeypatch.setattr(congress, "filing_trades", fake)
    errors = []
    df = congress.collect_trades(tmp_path, errors=errors, pause=0)
    assert list(df["member"]) == ["A"] and df["ticker"].iloc[0] == "AAPL"
    assert errors == ["Congresso 2: ValueError"]


def test_fundamentals_from_sec_fixture(tmp_path):
    import json

    from stocks import fundamentals as fu

    cf = json.loads((FIX.parent / "trader-analyst" / "companyfacts-CIK0000320193.json").read_text())
    m = fu.company_metrics("AAPL", None, tmp_path, companyfacts=cf)
    assert m["name"] == "Apple Inc." and m["revenue_growth_yoy"] == 16.4 and len(m["quarters"]) == 4
    assert fu.metrics_text(m) == ("📑 Bilanci (trimestre al 2026-06-27): ricavi in 12 mesi 466,8 Mld $, ricavi +16,4% "
                                  "e utile per azione +28,7% rispetto a un anno prima, margine netto 27,2%")
    # seconda lettura dalla cache, senza rete (sec=None)
    assert fu.company_metrics("AAPL", None, tmp_path) == m
