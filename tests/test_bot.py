import pandas as pd
import pytest

import fake_stocks as fk
from stocks import telegram_bot as tb
from stocks.config import DEFAULT_CONFIG, load_config, save_config

NOW = pd.Timestamp.now(tz="UTC")


class FakeAPI:
    def __init__(self, updates=None):
        self.sent = []
        self.updates = updates or []

    def send(self, chat_id, text, keyboard=True):
        self.sent.append((chat_id, text))

    def get_updates(self, offset, timeout=25):
        return [u for u in self.updates if u["update_id"] >= offset]

    def set_commands(self):
        pass

    def send_document(self, chat_id, path, caption=""):
        self.sent.append((chat_id, f"[file {path.name}]"))


def msg(text, chat=42, uid=1):
    return {"update_id": uid, "message": {"text": text, "chat": {"id": chat, "first_name": "Nico"}}}


@pytest.fixture
def bot(monkeypatch):
    tb.BOT_STATE.unlink(missing_ok=True)
    save_config({**DEFAULT_CONFIG, "telegram_token": "T", "telegram_chat_id": "42",
                 "watchlist": ["NVDA", "PFE", "AAPL", "JPM"], "test_previsioni": False})  # niente rete nei test
    b = tb.RadarBot(FakeAPI())
    b.snap = fk.snapshot(
        rating_rows=[("NVDA", NOW, "Goldman Sachs", "Goldman Sachs", 1.0, "up", "Neutral", "Buy", 220.0, 180.0)],
        holdings=pd.DataFrame([{"ticker": "NVDA", "shares_prev": 100, "shares_now": 120, "change_pct": 20.0,
                                "filer": "Vanguard"}]),
    )
    yield b
    save_config(dict(DEFAULT_CONFIG))


def last(b):
    return b.api.sent[-1][1]


def test_parse_commands_buttons_and_tickers():
    assert tb.RadarBot.parse("/titolo nvda") == ("titolo", "nvda")
    assert tb.RadarBot.parse("/compra@MioBot") == ("compra", "")
    assert tb.RadarBot.parse("🏆 Compra") == ("compra", "")
    assert tb.RadarBot.parse("brk-b") == ("titolo", "brk-b")
    assert tb.RadarBot.parse("ciao come stai?") == ("aiuto", "")


def test_first_start_registers_owner_then_private(monkeypatch):
    save_config({**DEFAULT_CONFIG, "telegram_token": "T", "telegram_chat_id": ""})
    b = tb.RadarBot(FakeAPI())
    b.handle_update(msg("ciao", chat=7))
    assert "/start" in last(b) and load_config()["telegram_chat_id"] == ""
    b.handle_update(msg("/start", chat=7))
    assert load_config()["telegram_chat_id"] == "7" and "risponde solo a te" in last(b)
    b.handle_update(msg("/compra", chat=99))
    assert b.api.sent[-1] == (99, "⛔ Questo è un bot privato.")
    save_config(dict(DEFAULT_CONFIG))


def test_buy_command_lists_best_candidates(bot):
    bot.handle_update(msg("/compra"))
    text = last(bot)
    assert text.startswith("🏆 Migliori candidati") and "1. NVDA (Nvidia)" in text
    assert "Goldman Sachs: upgrade a Buy" in text and "non consulenza" in text
    assert "🎯 BUY" in text and "SELL" in text and "STOP" in text


def test_ticker_detail_and_unknown_ticker(bot, monkeypatch):
    bot.handle_update(msg("NVDA"))
    text = last(bot)
    assert "📈 NVDA – Nvidia (Tecnologia)" in text and "🏦 Giudizi recenti" in text and "Goldman" in text
    assert "🎯 BUY" in text and "📊 Storico:" in text
    monkeypatch.setattr(tb.sources, "fetch_prices", lambda t: {})
    bot.handle_update(msg("/titolo ZZZZ"))
    assert "Nessun dato per ZZZZ" in last(bot)
    # titolo fuori watchlist: scaricato al momento
    monkeypatch.setattr(tb.sources, "fetch_prices", lambda t: {"TSLA": fk.prices(0.002, seed=9)})
    monkeypatch.setattr(tb.sources, "fetch_ratings", lambda t, firms: fk.ratings([]))
    bot.handle_update(msg("/titolo tsla"))
    assert "📈 TSLA – Tesla" in last(bot)


def test_info_commands(bot):
    for cmd, expected in [("/fed", "Tono della Fed"), ("/banche", "Goldman Sachs ⬆️ alza NVDA: Buy"),
                          ("/fondi", "Vanguard:"), ("/notizie pfizer", "Pfizer shares plunge"),
                          ("/evita", "PFE"), ("/classifica", "Classifica"), ("/watchlist", "4 titoli"),
                          ("/stato", "Bot attivo"), ("/boh", "Comando sconosciuto"), ("/start", "/compra")]:
        bot.handle_update(msg(cmd))
        assert expected in last(bot), (cmd, last(bot))


def test_watchlist_add_remove(bot):
    bot.handle_update(msg("/aggiungi tsla, amd"))
    assert load_config()["watchlist"][-2:] == ["TSLA", "AMD"]
    bot.handle_update(msg("/rimuovi TSLA"))
    assert "TSLA" not in load_config()["watchlist"]


def test_controlla_runs_check_and_replies(bot, monkeypatch):
    monkeypatch.setattr(tb, "run_once", lambda cfg, notify: {"message": "RIEPILOGO", "snapshot": bot.snap})
    bot.handle_update(msg("▶️ Controlla"))
    assert last(bot) == "RIEPILOGO" and "last_check" in bot.state


def test_poll_saves_offset_and_skips_handled(bot):
    bot.api.updates = [msg("/watchlist", uid=10), msg("/stato", uid=11)]
    assert bot.poll() == 2 and bot.state["offset"] == 12
    assert tb.RadarBot(FakeAPI()).state["offset"] == 12  # salvato su disco
    assert bot.poll() == 0


def test_errors_in_commands_are_reported(bot, monkeypatch):
    monkeypatch.setattr(tb.RadarBot, "cmd_fed", lambda self, a, c: 1 / 0)
    bot.handle_update(msg("/fed"))
    assert last(bot).startswith("⚠️ Errore: ZeroDivisionError")


def test_crypto_command(bot, monkeypatch):
    import ccxt
    from fake_exchange import FakeExchange

    monkeypatch.setattr(ccxt, "binance", FakeExchange, raising=False)
    bot.handle_update(msg("🪙 Crypto"))
    assert "Crypto (Binance" in last(bot) and "/USDT" in last(bot)


def test_telegram_api_send_splits_and_adds_keyboard():
    calls = []

    class S:
        def post(self, url, json, timeout):
            calls.append((url, json))
            return type("R", (), {"status_code": 200, "json": lambda self: {"ok": True, "result": {}}})()

    api = tb.TelegramAPI("TOK", session=S())
    api.send(1, "\n".join("x" * 100 for _ in range(100)))
    assert len(calls) == 3 and all(u.endswith("/sendMessage") for u, _ in calls)
    assert "reply_markup" in calls[-1][1] and "reply_markup" not in calls[0][1]
    assert calls[-1][1]["reply_markup"]["keyboard"][0] == ["🏆 Compra", "📉 Evita", "📊 Classifica"]


def test_run_forever_does_hourly_check_and_stops(bot, monkeypatch):
    import threading

    checks = []
    monkeypatch.setattr(tb, "run_once", lambda cfg, notify: checks.append(notify) or {
        "snapshot": bot.snap, "alerts": [], "pushed": [], "status": []})
    bot.api.updates = [msg("/watchlist", uid=5)]
    stop = threading.Event()
    t = threading.Thread(target=bot.run_forever, args=(stop,))
    t.start()
    for _ in range(50):
        if checks and bot.state.get("offset") == 6:
            break
        threading.Event().wait(0.1)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert checks == [True]  # controllo orario con invio degli avvisi
    assert "Watchlist" in bot.api.sent[0][1]


def test_test_command_sends_excel(bot):
    import previsioni as pv

    pv.JOURNAL_FILE.unlink(missing_ok=True)
    bot.handle_update(msg("/test"))
    assert "nessuna previsione" in last(bot)
    pv.save_journal([{"id": "x", "symbol": "SOL/USDT", "exchange": "binance", "timeframe": "1h", "horizon": 48,
                      "created": "2026-10-21T10:00:00+00:00", "signal_ts": 0, "price": 100.0, "score": 50.0,
                      "signal": "s", "buy": 99.5, "sell": 102.5, "stop": 98.0, "probability": 0.4,
                      "hist_trades": 3, "expected_margin_pct": 3.0, "status": "pending"}])
    pv.EXCEL_FILE.unlink(missing_ok=True)
    bot.handle_update(msg("🧪 Test"))
    assert bot.api.sent[-2][1] == "[file test_previsioni.xlsx]" and "1 previsioni" in last(bot)
    pv.JOURNAL_FILE.unlink(missing_ok=True)


def test_hourly_check_runs_forward_test(bot, monkeypatch):
    import previsioni as pv

    save_config({**load_config(), "test_previsioni": True})

    monkeypatch.setattr(tb, "run_once", lambda cfg, notify: {"snapshot": bot.snap, "alerts": [], "pushed": [],
                                                             "status": []})
    sent = []
    monkeypatch.setattr(tb, "notify_all", lambda cfg, title, text, short: sent.append(text))
    closed = {"symbol": "SOL/USDT", "status": "win", "ret_pct": 3.0, "expected_margin_pct": 3.0, "probability": 0.5}
    monkeypatch.setattr(pv, "run", lambda cfg: {"new": [], "closed": [closed], "stats": {"total": 1, "first": "x",
                        "pending": 0, "open": 0, "unfilled": 0, "closed": 0}})
    bot.hourly_check()
    assert sent and sent[0].startswith("✅ SOL/USDT: SELL raggiunto")
