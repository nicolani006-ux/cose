import json

import pandas as pd
import pytest

import fake_stocks as fk
from stocks import analysis, background, monitor, notify, sources
from stocks.config import STATE_FILE, REPORT_DIR

NOW = pd.Timestamp.now(tz="UTC")


# --------------------------------------------------------------------------- #
# Testo: sentiment, tono Fed, collegamento ai titoli
# --------------------------------------------------------------------------- #

def test_sentiment_and_rate_language():
    assert analysis.sentiment("Apple shares surge after earnings beat") > 0.5
    assert analysis.sentiment("Pfizer stock plunges on lawsuit") < -0.5
    # "rate cut" non deve contare come notizia negativa
    assert analysis.sentiment("Fed signals rate cut") == 0
    assert analysis.cb_tone("Committee decided to lower the target range; inflation easing") > 0
    assert analysis.cb_tone("Inflation remains elevated; policy will stay restrictive") < 0
    assert analysis.cb_tone("Board announces new members") is None


def test_tag_tickers_ignores_bank_acting_as_analyst():
    pats = analysis._name_patterns(["GS", "NVDA", "AAPL", "JPM", "T"])
    assert analysis.tag_tickers("Goldman Sachs upgrades Nvidia to Buy", pats) == ["NVDA"]
    assert analysis.tag_tickers("Goldman Sachs profit jumps on trading", pats) == ["GS"]
    assert analysis.tag_tickers("New iPhone sales (NASDAQ: AAPL) strong", pats) == ["AAPL"]
    assert analysis.tag_tickers("AT&T adds subscribers", pats) == ["T"]
    assert analysis.tag_tickers("JPMorgan strategists see S&P at 7000", pats) == []


def test_parse_google_news_feed():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
    <item><title>Fed holds rates steady - Reuters</title><link>https://news.google.com/a</link>
    <pubDate>Fri, 16 Oct 2026 14:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
    </channel></rss>"""
    items = sources.parse_feed(rss, "Federal Reserve", "notizie")
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "Fed holds rates steady" and it["publisher"] == "Reuters"
    assert it["published"] == pd.Timestamp("2026-10-16 14:00", tz="UTC")


def test_google_news_url_encodes_query():
    url = sources.google_news_url('"Goldman Sachs" outlook')
    assert url.startswith("https://news.google.com/rss/search?q=%22Goldman+Sachs%22+outlook+when%3A2d")


# --------------------------------------------------------------------------- #
# SEC 13F
# --------------------------------------------------------------------------- #

def _table(rows):
    body = "".join(
        f"<infoTable><nameOfIssuer>{n}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>X</cusip>"
        f"<value>1</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        + (f"<putCall>{pc}</putCall>" if pc else "") + "</infoTable>"
        for n, s, pc in rows
    )
    return f'<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">{body}</informationTable>'.encode()


def test_parse_13f_and_change():
    prev = sources.parse_13f_xml(_table([("APPLE INC", 100, None), ("NVIDIA CORPORATION", 50, None),
                                         ("ALPHABET INC", 10, None), ("ALPHABET INC", 10, None)]))
    latest = sources.parse_13f_xml(_table([("APPLE INC", 110, None), ("NVIDIA CORP", 25, None),
                                           ("NVIDIA CORP", 999, "Call"), ("ALPHABET INC", 30, None),
                                           ("INTELLIA THERAPEUTICS", 5, None)]))
    assert len(latest) == 4  # la riga con opzione call è esclusa
    ch = analysis.holdings_change(latest, prev, ["AAPL", "NVDA", "GOOGL", "INTC"]).set_index("ticker")
    assert ch.loc["AAPL", "change_pct"] == pytest.approx(10)
    assert ch.loc["NVDA", "change_pct"] == pytest.approx(-50)
    assert ch.loc["GOOGL", "change_pct"] == pytest.approx(50)  # classi A e C sommate
    assert "INTC" not in ch.index  # "INTELLIA" non è Intel


def test_normalize_issuer():
    assert analysis.normalize_issuer("JPMORGAN CHASE & CO") == analysis.normalize_issuer("JPMorgan Chase")
    assert analysis.normalize_issuer("AT&T INC") == "AT T"
    assert analysis.normalize_issuer("PROCTER AND GAMBLE CO") == analysis.normalize_issuer("Procter & Gamble")


# --------------------------------------------------------------------------- #
# Punteggio
# --------------------------------------------------------------------------- #

def test_rating_points_direction_and_decay():
    base = {"ticker": "NVDA", "bank": "Goldman Sachs", "weight": 1.0, "from_grade": "Neutral",
            "to_grade": "Buy", "pt_now": 200.0, "pt_prev": 150.0}
    up_now, desc = analysis.rating_points({**base, "action": "up", "date": NOW}, NOW)
    up_old, _ = analysis.rating_points({**base, "action": "up", "date": NOW - pd.Timedelta(days=20)}, NOW)
    down, _ = analysis.rating_points({**base, "action": "down", "to_grade": "Sell", "pt_now": 100.0,
                                      "date": NOW}, NOW)
    assert up_now == pytest.approx(30) and "Goldman Sachs: upgrade a Buy" in desc and "150→200$" in desc
    assert 0 < up_old < up_now
    assert down == pytest.approx(-30)


def test_score_rewards_trend_upgrades_and_good_news():
    snap = fk.snapshot(rating_rows=[
        ("NVDA", NOW, "Goldman Sachs", "Goldman Sachs", 1.0, "up", "Neutral", "Buy", 220.0, 180.0),
        ("PFE", NOW, "Morgan Stanley", "Morgan Stanley", 1.0, "down", "Overweight", "Equal-Weight", None, None),
    ])
    r = analysis.score_stocks(snap, ["NVDA", "PFE", "AAPL", "JPM"]).set_index("ticker")
    assert r.loc["NVDA", "score"] > 20 and "Acquisto" in r.loc["NVDA", "signal"]
    assert r.loc["PFE", "score"] < -20
    assert "Goldman Sachs: upgrade a Buy" in r.loc["NVDA", "reasons"]
    assert r.loc["NVDA", "s_macro"] > 0  # Fed accomodante favorisce la tecnologia
    assert r.index[0] == "NVDA"


# --------------------------------------------------------------------------- #
# Monitor: prima esecuzione, novità, niente doppioni
# --------------------------------------------------------------------------- #

@pytest.fixture
def clean_state(monkeypatch):
    STATE_FILE.unlink(missing_ok=True)
    for f in REPORT_DIR.glob("*"):
        f.unlink()
    sent = []
    monkeypatch.setattr(monitor, "notify_all", lambda cfg, title, text, short: sent.append(text) or ["ok"])
    return sent


CFG = {"watchlist": ["NVDA", "PFE", "AAPL", "JPM"], "livello_minimo_avviso": "media",
       "invia_riepilogo_sempre": False}


def test_monitor_first_run_then_only_new_alerts(clean_state):
    sent = clean_state
    r1 = monitor.run_once(CFG, snap=fk.snapshot())
    assert r1["first_run"] and r1["pushed"] == []
    assert "Monitoraggio attivato" in sent[0] and "Migliori candidati" in sent[0]
    assert "🎯 BUY" in sent[0]

    snap2 = fk.snapshot(
        rating_rows=[("NVDA", NOW, "Goldman Sachs", "Goldman Sachs", 1.0, "up", "Neutral", "Buy", 220.0, 180.0)],
        extra_news=[{"id": "fed2", "group": "Fed – discorsi", "kind": "banca_centrale", "publisher": "Fed",
                     "title": "Speech on monetary policy outlook", "summary": "", "link": "https://fed/2",
                     "published": NOW}],
    )
    r2 = monitor.run_once(CFG, snap=snap2)
    texts = [a["text"] for a in r2["pushed"]]
    assert any("Goldman Sachs alza il giudizio su NVDA" in t for t in texts)
    assert any("Speech on monetary policy" in t for t in texts)
    assert not any("FOMC statement" in t for t in texts)  # già vista al primo giro
    assert r2["pushed"][0]["level"] == "alta"
    assert len(sent) == 2

    r3 = monitor.run_once(CFG, snap=snap2)
    assert r3["pushed"] == [] and len(sent) == 2  # nessun doppione, nessun messaggio

    report = json.loads((REPORT_DIR / "ultimo_report.json").read_text(encoding="utf-8"))
    assert report["ranking"][0]["ticker"] == "NVDA"
    assert (REPORT_DIR / "storico_avvisi.csv").exists()


def test_monitor_alerts_8k_13f_and_signal_change(clean_state):
    monitor.save_state({"last_run": "x", "seen": {"vecchio": NOW.isoformat()}, "last_signal": {"NVDA": "⚪ Neutrale"}})
    filings = pd.DataFrame([{"ticker": "PFE", "form": "8-K", "date": "2026-10-17", "accession": "0001-26-1",
                             "items": "4.02,9.01", "url": "https://sec/x"}])
    holdings = pd.DataFrame([{"ticker": "NVDA", "shares_prev": 100, "shares_now": 120, "change_pct": 20.0,
                              "filer": "Vanguard"}])
    snap = fk.snapshot(filings=filings, holdings=holdings, rating_rows=[
        ("NVDA", NOW, "Goldman Sachs", "Goldman Sachs", 1.0, "up", "Neutral", "Buy", 220.0, 180.0)])
    snap.filings_13f = [{"filer": "Vanguard", "accession": "A1", "filed": "2026-08-14", "period": "2026-06-30"}]
    res = monitor.run_once(CFG, snap=snap)
    texts = " | ".join(a["text"] for a in res["pushed"])
    assert "PFE (Pfizer) – SEC 8-K: bilanci precedenti non affidabili" in texts
    assert "Nuovo 13F di Vanguard" in texts and "NVDA +20.0%" in texts
    assert "NVDA (Nvidia) passa da ⚪ Neutrale" in texts


def test_offline_first_run_does_not_flood_later(clean_state):
    sent = clean_state
    offline = fk.snapshot()
    offline.prices, offline.news = {}, offline.news.iloc[0:0]
    r0 = monitor.run_once(CFG, snap=offline)
    assert r0["first_run"] and sent == []  # niente da dire senza dati
    r1 = monitor.run_once(CFG, snap=fk.snapshot())
    assert r1["first_run"] and r1["pushed"] == [] and "Monitoraggio attivato" in sent[0]


def test_collect_survives_failing_sources(monkeypatch):
    def bad_feed(url, group, kind):
        raise ConnectionError("offline")

    class FakeSec:
        def __init__(self, *a, **k):
            pass

        def recent_8k(self, tickers):
            raise ConnectionError("offline")

        def latest_13f(self, ciks):
            raise ConnectionError("offline")

    monkeypatch.setattr(sources, "fetch_feed", bad_feed)
    monkeypatch.setattr(sources, "fetch_prices", lambda wl: {"NVDA": fk.prices(0.002)})
    monkeypatch.setattr(sources, "fetch_ratings", lambda wl, firms: fk.ratings([]))
    monkeypatch.setattr(sources, "Sec", FakeSec)
    snap = monitor.collect({**CFG, "sec_user_agent": "", "controlla_13f": True, "controlla_8k": True})
    assert "NVDA" in snap.prices and snap.news.empty
    assert any("SEC 8-K" in e for e in snap.errors) and any("13F" in e for e in snap.errors)
    assert not analysis.score_stocks(snap, CFG["watchlist"]).empty


# --------------------------------------------------------------------------- #
# Notifiche e pianificazione
# --------------------------------------------------------------------------- #

def test_split_message_respects_limit():
    text = "\n".join(f"riga {i} " + "x" * 50 for i in range(300))
    parts = notify.split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts).count("riga") == 300


def test_telegram_send_and_chat_ids():
    calls = []

    class S:
        def post(self, url, json, timeout):
            calls.append((url, json))
            return type("R", (), {"raise_for_status": lambda self: None})()

        def get(self, url, timeout):
            data = {"result": [{"message": {"chat": {"id": 42, "first_name": "Nico"}}}]}
            return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: data})()

    notify.send_telegram("TOKEN", "42", "ciao", session=S())
    assert calls[0][0] == "https://api.telegram.org/botTOKEN/sendMessage" and calls[0][1]["chat_id"] == "42"
    assert notify.find_chat_ids("TOKEN", session=S()) == ["42 (Nico)"]


def test_background_command_and_single_instance():
    cmd = background.command()
    assert cmd[-1].endswith("bot.py")
    lock = background.acquire_instance_lock()
    assert lock is not None and background.is_running()
    assert background.acquire_instance_lock() is None  # un secondo bot non parte
    lock.close()
    assert not background.is_running()
    background.request_stop()
    assert background.stop_requested()
    background.clear_stop()
    assert not background.stop_requested()


# --------------------------------------------------------------------------- #
# Interfaccia
# --------------------------------------------------------------------------- #

def test_stock_page_renders(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import stock_radar

    snap = fk.snapshot(rating_rows=[
        ("NVDA", NOW, "Goldman Sachs", "Goldman Sachs", 1.0, "up", "Neutral", "Buy", 220.0, 180.0)])
    monkeypatch.setattr(stock_radar, "collect", lambda cfg, progress=None: snap)
    monkeypatch.setattr(stock_radar, "load_snapshot", lambda **k: None)
    monkeypatch.setattr(stock_radar, "load_config", lambda: {
        **__import__("stocks.config", fromlist=["x"]).DEFAULT_CONFIG, "watchlist": CFG["watchlist"]})

    def page():
        import stock_radar as sr
        sr.main()

    at = AppTest.from_function(page, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6
    assert at.dataframe[0].value.iloc[0]["ticker"] == "NVDA"
    assert any("Goldman Sachs" in str(d.value.to_dict()) for d in at.dataframe)


def test_app_navigation_has_both_pages():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    probe = ('st.write(page.title)\nst.write(",".join(p()._url_path for p in '
             '(nav.crypto_page, nav.stocks_page, nav.settings_page)))')
    at = AppTest.from_string(src.replace("page.run()", probe), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert at.markdown[0].value == "Crypto"
    assert at.markdown[1].value == "crypto,azioni,impostazioni"


def test_settings_page_opens_without_loading_data(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import settings_page

    def page():
        import settings_page as sp
        sp.main()

    at = AppTest.from_function(page, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert [h.value for h in at.header] == ["🤖 Bot Telegram", "🔔 Avvisi", "📡 Fonti e watchlist"]
    at.text_input[0].input("123:ABC")
    at.button[0].click().run()  # Salva token
    assert monitor.load_config()["telegram_token"] == "123:ABC"
    assert not at.exception, at.exception
    assert any("Avvia bot" in b.label for b in at.button)
    from stocks.config import DEFAULT_CONFIG, save_config
    save_config(dict(DEFAULT_CONFIG))
    assert settings_page is not None


def test_collect_reports_progress_and_times_out(monkeypatch):
    import threading

    release = threading.Event()

    def slow_ratings(wl, firms):
        release.wait(10)
        return fk.ratings([])

    monkeypatch.setattr(sources, "fetch_feed", lambda url, group, kind: [])
    monkeypatch.setattr(sources, "fetch_prices", lambda wl: {"NVDA": fk.prices(0.002)})
    monkeypatch.setattr(sources, "fetch_ratings", slow_ratings)
    seen = []
    snap = monitor.collect({**CFG, "sec_user_agent": "x", "controlla_13f": False, "controlla_8k": False},
                           progress=lambda label, ok: seen.append((label, ok)), timeout=1)
    release.set()
    assert ("Prezzi", True) in seen and ("Notizie e comunicati", True) in seen
    assert ("Giudizi delle banche", False) in seen
    assert any("tempo scaduto" in e for e in snap.errors)
    assert "NVDA" in snap.prices and snap.ratings.empty


def test_snapshot_roundtrip_on_disk():
    snap = fk.snapshot()
    monitor.save_snapshot(snap)
    assert monitor.load_snapshot(60, snap.watchlist).fetched_at == snap.fetched_at
    assert monitor.load_snapshot(60, ["ALTRO"]) is None  # watchlist cambiata


def test_background_status_starting_and_crash_log(monkeypatch):
    import subprocess
    import sys

    background.STATUS_FILE.unlink(missing_ok=True)
    background.write_status(stato="in esecuzione", telegram="ok")
    background.write_status(ultimo_errore=None)
    st_ = background.read_status()
    assert st_["stato"] == "in esecuzione" and st_["telegram"] == "ok" and "aggiornato" in st_

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)) or type("P", (), {"pid": 7})())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert background.start() == 7
    cmd, kw = calls[0]
    assert cmd == [sys.executable, "--bot"] and kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert background.starting()  # avvio richiesto, bot non ancora pronto
    background.STARTING_FILE.write_text("0")  # richiesta troppo vecchia
    assert not background.starting()

    background.LOG_FILE.unlink(missing_ok=True)
    background.log_crash("Traceback: ImportError boom")
    assert "ERRORE AVVIO" in background.log_tail() and "boom" in background.log_tail()


def test_settings_start_button_waits_and_reports_failure(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from stocks.config import DEFAULT_CONFIG, save_config

    save_config({**DEFAULT_CONFIG, "telegram_token": "123:ABC"})
    background.LOG_FILE.write_text("2026-10-25 riga di prova\nTelegram: Unauthorized\n", encoding="utf-8")

    def page():
        import settings_page
        from stocks import background as bg

        bg.start = lambda: 1
        bg.wait_until_running = lambda *a, **k: False  # il bot non parte
        settings_page.bot_username = lambda token: "MioBot"
        settings_page.main()

    at = AppTest.from_function(page, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    assert any("Bot fermo" in e.value for e in at.error)
    next(b for b in at.button if "Avvia bot" in b.label).click().run()
    assert not at.exception, at.exception
    assert any("non è partito" in e.value for e in at.error)
    assert any("Unauthorized" in c.value for c in at.code)
    save_config(dict(DEFAULT_CONFIG))
