"""Avvisi meno rumorosi: notizie doppie, notizie generiche, ore di silenzio, aggiornamento dello stato."""

import pandas as pd
import pytest

import fake_stocks as fk
from stocks import monitor
from stocks.config import REPORT_DIR, STATE_FILE

NOW = fk.NOW
CFG = {"watchlist": ["NVDA", "PFE", "AAPL", "JPM"], "livello_minimo_avviso": "media", "invia_riepilogo_sempre": False}


@pytest.fixture
def sent(monkeypatch):
    STATE_FILE.unlink(missing_ok=True)
    for f in REPORT_DIR.glob("*"):
        f.unlink()
    out = []
    monkeypatch.setattr(monitor, "notify_all", lambda cfg, title, text, short: out.append(text) or ["ok"])
    return out


def news(i, title, group="Wall Street", kind="notizie"):
    return {"id": f"x{i}", "group": group, "kind": kind, "publisher": "Reuters", "title": title, "summary": "",
            "link": f"https://news/{i}", "published": NOW}


def test_news_key_ignores_case_punctuation_and_link():
    assert monitor.news_key("Nvidia shares SURGE!") == monitor.news_key("nvidia shares surge")
    assert monitor.news_key("Nvidia shares surge") != monitor.news_key("Nvidia shares plunge")


def test_same_story_from_two_searches_is_one_alert():
    snap = fk.snapshot(extra_news=[news(1, "Nvidia shares surge after record earnings"),
                                   news(2, "Nvidia shares surge after record earnings", group="Goldman Sachs")])
    alerts = monitor.build_alerts(snap, pd.DataFrame(), {})
    assert sum("record earnings" in a["text"] for a in alerts) == 1


def test_generic_news_without_ticker_is_only_info():
    snap = fk.snapshot(extra_news=[news(1, "Stocks surge to record high as markets rally strongly")])
    a = next(a for a in monitor.build_alerts(snap, pd.DataFrame(), {}) if "record high" in a["text"])
    assert a["level"] == "info"


def test_quiet_hours_parsing():
    assert monitor.in_quiet_hours("23-7", 23) and monitor.in_quiet_hours("23-7", 3)
    assert not monitor.in_quiet_hours("23-7", 7) and not monitor.in_quiet_hours("23-7", 12)
    assert monitor.in_quiet_hours("1-5", 2) and not monitor.in_quiet_hours("1-5", 6)
    assert not monitor.in_quiet_hours("", 3) and not monitor.in_quiet_hours("boh", 3)


def test_quiet_hours_defer_medium_alerts(sent, monkeypatch):
    monitor.run_once(CFG, snap=fk.snapshot())  # primo giro: tutto già visto
    monkeypatch.setattr(monitor, "in_quiet_hours", lambda spec, hour: True)
    snap = fk.snapshot(extra_news=[{**news(9, "Speech on the economic outlook", group="Fed – discorsi",
                                           kind="banca_centrale")}])
    r = monitor.run_once({**CFG, "ore_silenzio": "0-23"}, snap=snap)
    assert r["pushed"] == []  # discorso Fed = media: rimandato
    monkeypatch.setattr(monitor, "in_quiet_hours", lambda spec, hour: False)
    r = monitor.run_once(CFG, snap=snap)
    assert any("economic outlook" in a["text"] for a in r["pushed"])  # arriva finite le ore di silenzio


def test_old_state_does_not_resend_news_with_new_keys(sent):
    monitor.save_state({"seen": {"news:vecchia-chiave": NOW.isoformat()}, "last_signal": {}})
    snap = fk.snapshot(extra_news=[news(5, "Nvidia stock soars on huge AI deal")])
    r = monitor.run_once(CFG, snap=snap)
    assert not any(a["key"].startswith("news:") for a in r["pushed"])
    assert monitor.load_state()["schema"] == monitor.STATE_SCHEMA
