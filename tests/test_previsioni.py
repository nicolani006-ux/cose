import numpy as np
import pandas as pd
import pytest

import previsioni as pv

H = 3_600_000
T0 = 1_760_000_000_000  # ms, candela del segnale


def radar(last_candle=T0):
    rows = []
    for sym in ["WIN/USDT", "LOSS/USDT", "NOFILL/USDT", "WAIT/USDT", "OPEN/USDT"]:
        rows.append({"symbol": sym, "price": 100.0, "score": 55.0, "signal": "🟢 FORTE RIALZO", "buy": 99.5,
                     "sell": 102.5, "stop": 98.0, "bt_wins": 4, "bt_trades": 10, "bt_win_rate": 40.0,
                     "bt_avg_ret": 1.0, "last_candle": last_candle})
    rows.append({**rows[0], "symbol": "WEAK/USDT", "score": 5.0})  # nessun segnale d'acquisto
    rows.append({**rows[0], "symbol": "USDX/USDT", "buy": 1.0, "sell": 1.002, "stop": 0.999})  # margine troppo basso
    return pd.DataFrame(rows)


def candles(spec):
    """spec: lista di (open, high, low, close) dalla candela del segnale in poi."""
    return [[T0 + i * H, o, h, lo, c, 1.0] for i, (o, h, lo, c) in enumerate(spec)]


FLAT = (100, 100.4, 99.9, 100)
SERIES = {
    "WIN/USDT": candles([FLAT, (100, 100.2, 99.4, 99.8), (99.8, 103, 99.7, 102.8)] + [FLAT] * 60),
    "LOSS/USDT": candles([FLAT, (100, 100.2, 99.4, 99.6), (99.6, 99.7, 97.0, 97.5)] + [FLAT] * 60),
    "NOFILL/USDT": candles([FLAT] + [(101, 101.5, 100.6, 101)] * 60),
    "WAIT/USDT": candles([FLAT]),
    "OPEN/USDT": candles([FLAT, (100, 100.2, 99.4, 99.8)] + [FLAT] * 5),
}


def fetch(exchange, symbol, timeframe, since):
    return SERIES[symbol]


@pytest.fixture(autouse=True)
def clean():
    pv.JOURNAL_FILE.unlink(missing_ok=True)
    pv.EXCEL_FILE.unlink(missing_ok=True)
    yield


def test_probability_is_shrunk_towards_global_rate():
    assert pv.probability(0, 0, 0.4) == pytest.approx(0.4)        # nessun dato: media generale
    assert pv.probability(2, 2, 0.4) == pytest.approx(4 / 7)      # 2 su 2 non vuol dire 100%
    assert pv.probability(60, 100, 0.4) == pytest.approx(62 / 105)


def test_forward_test_end_to_end():
    cfg = {"test_exchange": "binance", "test_timeframe": "1h"}
    r1 = pv.run(cfg, scan=lambda *a: radar(), fetch=lambda *a: [])
    assert len(r1["new"]) == 5 and r1["closed"] == []  # WEAK non ha segnale
    assert all(e["status"] == "pending" for e in pv.load_journal())

    r2 = pv.run(cfg, scan=lambda *a: radar(), fetch=fetch)
    status = {e["symbol"]: e for e in pv.load_journal()}
    assert {k: v["status"] for k, v in status.items()} == {
        "WIN/USDT": "win", "LOSS/USDT": "loss", "NOFILL/USDT": "unfilled", "WAIT/USDT": "pending", "OPEN/USDT": "open"}
    assert sorted(e["symbol"] for e in r2["closed"]) == ["LOSS/USDT", "WIN/USDT"]
    assert r2["new"] == []  # stesse candele: nessun doppione
    win, loss = status["WIN/USDT"], status["LOSS/USDT"]
    assert win["entry"] == 99.5 and win["exit_price"] == 102.5 and win["ret_pct"] == pytest.approx(3.015, abs=1e-3)
    assert loss["exit_price"] == 98.0 and loss["ret_pct"] == pytest.approx(-1.508, abs=1e-3)
    assert win["net_pct"] < win["ret_pct"] and win["hours"] == pytest.approx(2)
    assert win["chg_horizon_pct"] == pytest.approx(0) and "chg_horizon_pct" not in status["WAIT/USDT"]

    s = r2["stats"]
    assert (s["total"], s["closed"], s["wins"], s["losses"], s["unfilled"], s["open"], s["pending"]) == (5, 2, 1, 1, 1, 1, 1)
    assert s["win_rate"] == 50 and s["prob_mean"] == pytest.approx(pv.probability(4, 10, 0.4) * 100)
    assert s["fill_rate"] == pytest.approx(75)  # 3 eseguite su 4 concluse/aperte
    assert s["total_eur"] == pytest.approx((win["net_pct"] + loss["net_pct"]), abs=1e-9)
    assert s["profit_factor"] > 1

    # nuova candela di segnale: le coin chiuse possono avere una nuova previsione, quelle attive no
    r3 = pv.run(cfg, scan=lambda *a: radar(T0 + 10 * H), fetch=fetch)
    assert sorted(e["symbol"] for e in r3["new"]) == ["LOSS/USDT", "NOFILL/USDT", "WIN/USDT"]


def test_excel_layout_and_statistics():
    from openpyxl import load_workbook

    cfg = {"test_exchange": "binance", "test_timeframe": "1h"}
    pv.run(cfg, scan=lambda *a: radar(), fetch=lambda *a: [])
    pv.run(cfg, scan=lambda *a: radar(), fetch=fetch)
    wb = load_workbook(pv.EXCEL_FILE)
    assert wb.sheetnames == ["Previsioni", "Statistiche", "Per crypto", "Calibrazione", "Andamento"]
    ws = wb["Previsioni"]
    assert [ws.cell(1, i).value for i in range(1, 6)] == [
        "Crypto", "Probabilità di rialzo ipotizzata", "Prezzo BUY → SELL ipotizzato",
        "Margine di guadagno ipotizzato", "Guadagno effettivo"]
    rows = {ws.cell(r, 1).value: [ws.cell(r, c).value for c in range(1, 7)] for r in range(2, ws.max_row + 1)}
    assert rows["WIN/USDT"][2] == "BUY 99.5 → SELL 102.5"
    assert rows["WIN/USDT"][3] == pytest.approx(0.030150, abs=1e-5)  # margine ipotizzato
    assert rows["WIN/USDT"][4] == pytest.approx(0.03015, abs=1e-5)   # guadagno effettivo
    assert rows["LOSS/USDT"][4] < 0 and rows["NOFILL/USDT"][4] is None and "non eseguito" in rows["NOFILL/USDT"][5]
    assert ws.cell(2, 2).number_format == "0%" and ws.cell(2, 5).number_format == "0.00%"
    stats = {r[0].value: r[1].value for r in wb["Statistiche"].iter_rows(min_row=2)}
    assert stats["Operazioni chiuse"] == 2 and stats["Successo reale (SELL prima dello STOP)"] == 0.5
    assert "Brier score" in stats and "Profit factor" in stats
    assert wb["Andamento"].max_row == 3 and len(wb["Andamento"]._charts) == 1
    assert wb["Per crypto"].max_row == 3


def test_summary_and_closed_text():
    assert "nessuna previsione" in pv.summary_text({"total": 0})
    cfg = {"test_exchange": "binance", "test_timeframe": "1h"}
    pv.run(cfg, scan=lambda *a: radar(), fetch=lambda *a: [])
    r = pv.run(cfg, scan=lambda *a: radar(), fetch=fetch)
    text = pv.summary_text(r["stats"])
    assert "5 previsioni" in text and "Successo reale 50%" in text and "100€" in text
    win = next(e for e in r["closed"] if e["symbol"] == "WIN/USDT")
    assert pv.closed_text(win).startswith("✅ WIN/USDT: SELL raggiunto, +3.02%")


def test_fetch_errors_are_kept_for_retry():
    cfg = {"test_exchange": "binance", "test_timeframe": "1h"}
    pv.run(cfg, scan=lambda *a: radar(), fetch=lambda *a: [])

    def boom(*a):
        raise ConnectionError("offline")

    pv.run(cfg, scan=lambda *a: pd.DataFrame(), fetch=boom)
    j = pv.load_journal()
    assert all(e["status"] == "pending" and "offline" in e["last_error"] for e in j)
    assert np.isnan(pv.to_frame(j)["ret_pct"]).all()


def test_test_page_renders():
    from streamlit.testing.v1 import AppTest

    cfg = {"test_exchange": "binance", "test_timeframe": "1h"}
    pv.run(cfg, scan=lambda *a: radar(), fetch=lambda *a: [])
    pv.run(cfg, scan=lambda *a: radar(), fetch=fetch)

    def page():
        import test_page
        test_page.main()

    at = AppTest.from_function(page, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert at.metric[0].value == "5" and at.metric[2].value == "50%"
    assert len(at.dataframe[0].value) == 5


def test_excel_cli(monkeypatch, capsys):
    import excel_previsioni

    assert excel_previsioni.main(["--solo-excel"]) == 0
    assert pv.EXCEL_FILE.exists() and "nessuna previsione" in capsys.readouterr().out

    monkeypatch.setattr(pv, "run", lambda cfg: {"new": [], "closed": [], "stats": {"total": 0},
                                                "error": "NetworkError: offline"})
    assert excel_previsioni.main([]) == 2
    assert "Exchange non raggiungibile" in capsys.readouterr().out


def test_run_survives_unreachable_exchange():
    def down(*a):
        raise ConnectionError("offline")

    res = pv.run({}, scan=down, fetch=down)
    assert res["error"].startswith("ConnectionError") and pv.EXCEL_FILE.exists()
