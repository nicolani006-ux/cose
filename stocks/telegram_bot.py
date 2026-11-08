"""Bot Telegram interattivo: risponde ai comandi e fa il controllo ogni ora.

Avvio:  python bot.py              (resta acceso: comandi + controllo orario)
        python bot.py --una-volta  (risponde ai messaggi in attesa ed esce)
"""

from __future__ import annotations

import argparse
import json
import os
import logging
import re
import threading
import time
from logging.handlers import RotatingFileHandler

import pandas as pd
import requests

from indicators import stat_text
from stocks import background, sources
from stocks.analysis import macro_tone, score_stocks
from stocks.config import ANALYST_FIRMS, DATA_DIR, REPORT_DIR, load_config, save_config, ticker_info
from stocks.monitor import Snapshot, collect, load_snapshot, run_once
from stocks.notify import notify_all, split_message

log = logging.getLogger("radar.bot")
BOT_STATE = DATA_DIR / "bot_stato.json"

COMMANDS = [
    ("compra", "Migliori titoli da comprare ora"),
    ("evita", "Titoli da evitare"),
    ("classifica", "Classifica di tutta la watchlist"),
    ("titolo", "Analisi di un titolo, es. /titolo NVDA"),
    ("fed", "Tono della Fed e ultimi comunicati"),
    ("banche", "Ultimi giudizi di Goldman, JPMorgan, Morgan Stanley, BofA"),
    ("notizie", "Ultime notizie, es. /notizie goldman"),
    ("fondi", "Cosa comprano BlackRock, Vanguard, State Street"),
    ("avvisi", "Ultimi avvisi"),
    ("controlla", "Esegui subito il controllo completo"),
    ("crypto", "Migliori criptovalute ora"),
    ("test", "Risultati del test delle previsioni + file Excel"),
    ("watchlist", "Titoli seguiti"),
    ("aggiungi", "Aggiungi titoli, es. /aggiungi TSLA AMD"),
    ("rimuovi", "Togli titoli, es. /rimuovi TSLA"),
    ("stato", "Stato del bot e delle fonti"),
    ("aiuto", "Elenco dei comandi"),
]
BUTTONS = {
    "🏆 Compra": "compra", "📉 Evita": "evita", "📊 Classifica": "classifica",
    "🏛️ Fed": "fed", "🏦 Banche": "banche", "🏢 Fondi": "fondi",
    "📰 Notizie": "notizie", "🔔 Avvisi": "avvisi", "▶️ Controlla": "controlla",
    "🪙 Crypto": "crypto", "🧪 Test": "test", "ℹ️ Aiuto": "aiuto",
}
KEYBOARD = [list(BUTTONS)[i:i + 3] for i in range(0, len(BUTTONS), 3)]
DISCLAIMER = "ℹ️ Indicazioni automatiche, non consulenza finanziaria."


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str, session=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.s = session or requests.Session()

    def call(self, method: str, http_timeout: float = 30, **params):
        r = self.s.post(self.base + method, json=params, timeout=http_timeout)
        try:
            data = r.json()
        except ValueError as e:
            raise TelegramError(f"HTTP {r.status_code}") from e
        if not data.get("ok"):
            raise TelegramError(data.get("description", f"HTTP {r.status_code}"))
        return data["result"]

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        return self.call("getUpdates", http_timeout=timeout + 15, offset=offset, timeout=timeout,
                         allowed_updates=["message"])

    def send(self, chat_id, text: str, keyboard: bool = True) -> None:
        parts = split_message(text)
        for i, part in enumerate(parts):
            params = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if keyboard and i == len(parts) - 1:
                params["reply_markup"] = {"keyboard": KEYBOARD, "resize_keyboard": True, "is_persistent": True}
            self.call("sendMessage", **params)

    def send_document(self, chat_id, path, caption: str = "") -> None:
        with open(path, "rb") as f:
            r = self.s.post(self.base + "sendDocument", data={"chat_id": chat_id, "caption": caption[:1000]},
                            files={"document": (path.name, f)}, timeout=60)
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(data.get("description", f"HTTP {r.status_code}"))

    def set_commands(self) -> None:
        self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])


def plan_line(r) -> str:
    """🎯 BUY / SELL / STOP con la probabilità (storia + Time Machine)."""
    if pd.isna(r.get("buy")):
        return "🎯 Livelli non disponibili (storico insufficiente)"
    p = r.get("probability")
    prob = f" · probabilità SELL {p:.0f}%" if p is not None and not pd.isna(p) else ""
    return (f"🎯 BUY {r['buy']:,.2f}$ · SELL {r['sell']:,.2f}$ (+{r['upside_pct']:.1f}%) · "
            f"STOP {r['stop']:,.2f}$ (-{r['risk_pct']:.1f}%){prob}")


def _pct(v) -> str:
    return "n/d" if v is None or pd.isna(v) else f"{v:+.1f}%"


class RadarBot:
    def __init__(self, api: TelegramAPI, check_every_min: int = 60):
        self.api = api
        self.check_every = check_every_min * 60
        self.snap: Snapshot | None = None
        self._check_lock = threading.Lock()
        self._token: str | None = None
        self.state = self._load_state()

    # -- stato ---------------------------------------------------------------- #
    @staticmethod
    def _load_state() -> dict:
        try:
            return json.loads(BOT_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        BOT_STATE.parent.mkdir(parents=True, exist_ok=True)
        BOT_STATE.write_text(json.dumps(self.state), encoding="utf-8")

    @property
    def cfg(self) -> dict:
        return load_config()  # riletta ogni volta: le modifiche fatte nell'app valgono subito

    # -- dati ----------------------------------------------------------------- #
    def snapshot(self, chat_id=None, max_age_min: int = 30) -> Snapshot:
        cfg = self.cfg
        fresh = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=max_age_min)
        if self.snap is not None and self.snap.fetched_at >= fresh and set(self.snap.watchlist) == set(cfg["watchlist"]):
            return self.snap
        disk = load_snapshot(max_age_min, cfg["watchlist"])
        if disk is not None:
            self.snap = disk
            return disk
        if chat_id is not None:
            self.api.send(chat_id, "⏳ Raccolgo prezzi, giudizi delle banche, notizie e dati SEC… (circa un minuto)",
                          keyboard=False)
        self.snap = collect(cfg)
        return self.snap

    def ranking(self, chat_id=None) -> pd.DataFrame:
        snap = self.snapshot(chat_id)
        return score_stocks(snap, snap.watchlist)

    # -- messaggi ------------------------------------------------------------- #
    def handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None or not text:
            return
        cfg = self.cfg
        owner = str(cfg.get("telegram_chat_id") or "")
        if not owner:
            if text.startswith("/start"):
                cfg["telegram_chat_id"] = str(chat_id)
                save_config(cfg)
                self.api.send(chat_id, "✅ Ciao! Questo bot ora risponde solo a te e ti manderà gli avvisi.\n\n" + self.cmd_aiuto())
            else:
                self.api.send(chat_id, "Scrivi /start per collegare questo bot.", keyboard=False)
            return
        if str(chat_id) != owner:
            self.api.send(chat_id, "⛔ Questo è un bot privato.", keyboard=False)
            return
        cmd, arg = self.parse(text)
        try:
            reply = self.dispatch(cmd, arg, chat_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Errore nel comando %s", cmd)
            reply = f"⚠️ Errore: {type(e).__name__}: {e}"
        if reply:
            self.api.send(chat_id, reply)

    @staticmethod
    def parse(text: str) -> tuple[str, str]:
        if text in BUTTONS:
            return BUTTONS[text], ""
        if text.startswith("/"):
            head, _, arg = text[1:].partition(" ")
            return head.split("@")[0].lower(), arg.strip()
        if re.fullmatch(r"[A-Za-z]{1,5}(?:[.-][A-Za-z])?", text):
            return "titolo", text
        return "aiuto", ""

    def dispatch(self, cmd: str, arg: str, chat_id) -> str:
        handler = getattr(self, f"cmd_{'aiuto' if cmd in ('start', 'help') else cmd}", None)
        if handler is None:
            return "Comando sconosciuto.\n\n" + self.cmd_aiuto()
        return handler(arg, chat_id)

    # -- comandi -------------------------------------------------------------- #
    def cmd_aiuto(self, *_):
        lines = ["📊 Radar Ultimate – comandi:"] + [f"/{c} – {d}" for c, d in COMMANDS]
        lines.append("\nPuoi anche scrivere solo il ticker (es. NVDA) o usare i pulsanti qui sotto.")
        return "\n".join(lines)

    def _rank_lines(self, rows: pd.DataFrame, reasons: bool, plan: bool = False) -> list[str]:
        out = []
        for i, r in enumerate(rows.itertuples(), 1):
            line = f"{i}. {r.ticker} ({r.name}) {r.score:+.0f} {r.signal} · {r.price:,.2f}$ ({_pct(r.chg_1d)} oggi)"
            if plan:
                line += "\n   " + plan_line(r._asdict())
            if reasons:
                line += "\n   " + r.reasons[:300]
            out.append(line)
        return out

    def cmd_compra(self, arg, chat_id):
        rk = self.ranking(chat_id)
        if rk.empty:
            return "⚠️ Prezzi non disponibili al momento."
        buy = rk[rk["score"] >= 20].head(int(arg) if arg.isdigit() else 5)
        if buy.empty:
            return "🏆 Nessun titolo ha ora un segnale di acquisto chiaro.\n\n" + DISCLAIMER
        return "\n".join(["🏆 Migliori candidati all'acquisto ora:", *self._rank_lines(buy, True, plan=True), "",
                          DISCLAIMER])

    def cmd_evita(self, arg, chat_id):
        rk = self.ranking(chat_id)
        bad = rk[rk["score"] <= -20].sort_values("score").head(5) if not rk.empty else rk
        if bad.empty:
            return "📉 Nessun titolo con segnale negativo forte."
        return "\n".join(["📉 Titoli da evitare ora:", *self._rank_lines(bad, True), "", DISCLAIMER])

    def cmd_classifica(self, arg, chat_id):
        rk = self.ranking(chat_id)
        if rk.empty:
            return "⚠️ Prezzi non disponibili al momento."
        return "\n".join(["📊 Classifica watchlist:", *self._rank_lines(rk, False)])

    def cmd_titolo(self, arg, chat_id):
        t = arg.strip().upper().replace(".", "-")
        if not t:
            return "Scrivi il ticker, es. /titolo NVDA"
        snap = self.snapshot(chat_id)
        rk = score_stocks(snap, snap.watchlist)
        ratings = snap.ratings
        if rk.empty or t not in set(rk["ticker"]):
            prices = sources.fetch_prices([t])
            if t not in prices:
                return f"❓ Nessun dato per {t}. Controlla il ticker (titoli USA come su Yahoo Finance)."
            ratings = sources.fetch_ratings([t], ANALYST_FIRMS)
            mini = Snapshot(fetched_at=pd.Timestamp.now(tz="UTC"), prices=prices, ratings=ratings,
                            news=snap.news, watchlist=[t])
            rk = score_stocks(mini, [t])
        r = rk.set_index("ticker").loc[t]
        name, sector, _, _ = ticker_info(t)
        lines = [
            f"📈 {t} – {name} ({sector})",
            f"Prezzo {r['price']:,.2f}$ · oggi {_pct(r['chg_1d'])} · 1 mese {_pct(r['chg_1m'])}",
            f"Punteggio {r['score']:+.0f} → {r['signal']}",
            f"(tecnico {r['s_tecnico']:+.0f} · banche {r['s_analisti']:+.0f} · notizie {r['s_notizie']:+.0f} · "
            f"13F {r['s_istituzioni']:+.0f} · Fed {r['s_macro']:+.0f})",
            "", plan_line(r),
            f"📊 Storico: {stat_text(r, 'giorni')}",
            *([f"⏱️ Time Machine: {r['mc_verdict']}"] if isinstance(r.get("mc_verdict"), str) else []),
            *(["⚠️ Il segnale attuale non è d'acquisto: lo STOP serve se hai già il titolo, il BUY per un "
               "ingresso futuro."] if r["score"] < 20 else []),
            "", "Motivi:", *[f"• {x}" for x in r["reasons"].split(" · ")],
        ]
        if not ratings.empty and t in set(ratings["ticker"]):
            lines += ["", "🏦 Giudizi recenti:"]
            for g in ratings[ratings["ticker"] == t].head(5).itertuples():
                lines.append(f"• {g.date:%d/%m} {g.bank}: {g.action} {g.from_grade + ' → ' if g.from_grade else ''}{g.to_grade}")
        if not snap.news.empty:
            mine = snap.news[snap.news["tickers"].map(lambda x: t in x)].head(3)
            if not mine.empty:
                lines += ["", "📰 Notizie:"] + [f"• {n.title} ({n.publisher})\n  {n.link}" for n in mine.itertuples()]
        lines += ["", DISCLAIMER]
        return "\n".join(lines)

    def cmd_fed(self, arg, chat_id):
        snap = self.snapshot(chat_id)
        tone = macro_tone(snap.news)
        word = "accomodante 🕊️ (tassi giù)" if tone > 0.15 else "restrittivo 🦅 (tassi su)" if tone < -0.15 else "neutro"
        lines = [f"🏛️ Tono della Fed negli ultimi 10 giorni: {word} ({tone:+.2f})", ""]
        cb = snap.news[snap.news["kind"].str.startswith("banca_centrale")] if not snap.news.empty else snap.news
        if cb.empty:
            lines.append("Nessun comunicato scaricato.")
        for n in cb.sort_values("published", ascending=False).head(8).itertuples():
            when = f"{n.published:%d/%m %H:%M}" if pd.notna(n.published) else ""
            lines.append(f"• {when} {n.group}: {n.title}\n  {n.link}")
        return "\n".join(lines)

    def cmd_banche(self, arg, chat_id):
        snap = self.snapshot(chat_id)
        if snap.ratings.empty:
            return "🏦 Nessun giudizio recente delle grandi banche sui titoli della watchlist."
        verbs = {"up": "⬆️ alza", "down": "⬇️ abbassa", "init": "🆕 inizia", "main": "conferma", "reit": "ribadisce"}
        lines = ["🏦 Ultimi giudizi delle banche d'affari:"]
        for g in snap.ratings.head(15).itertuples():
            pt = f", target {g.pt_now:.0f}$" if g.pt_now and not pd.isna(g.pt_now) else ""
            lines.append(f"• {g.date:%d/%m} {g.bank} {verbs.get(g.action, g.action)} {g.ticker}: {g.to_grade}{pt}")
        return "\n".join(lines)

    def cmd_notizie(self, arg, chat_id):
        snap = self.snapshot(chat_id)
        news = snap.news
        if news.empty:
            return "📰 Nessuna notizia scaricata."
        if arg:
            q = arg.lower()
            news = news[news["group"].str.lower().str.contains(q, regex=False)
                        | news["title"].str.lower().str.contains(q, regex=False)]
        news = news.sort_values("published", ascending=False, na_position="last").head(10)
        if news.empty:
            return f"📰 Nessuna notizia su «{arg}»."
        lines = [f"📰 Ultime notizie{' su ' + arg if arg else ''}:"]
        for n in news.itertuples():
            mood = "📈 " if n.sentiment >= 0.34 else "📉 " if n.sentiment <= -0.34 else ""
            lines.append(f"• {mood}{n.title} – {n.publisher} [{n.group}]\n  {n.link}")
        return "\n".join(lines)

    def cmd_fondi(self, arg, chat_id):
        snap = self.snapshot(chat_id)
        if snap.holdings.empty:
            return "🏢 Dati 13F non disponibili (imposta nome ed email per la SEC nelle impostazioni dell'app)."
        lines = ["🏢 Variazioni di azioni nell'ultimo trimestre (moduli 13F):"]
        for filer, h in snap.holdings.groupby("filer"):
            up = h[h["change_pct"] > 0].nlargest(5, "change_pct")
            down = h[h["change_pct"] < 0].nsmallest(5, "change_pct")
            lines.append(f"\n{filer}:")
            if not up.empty:
                lines.append("  ⬆️ " + ", ".join(f"{r.ticker} {r.change_pct:+.1f}%" for r in up.itertuples()))
            if not down.empty:
                lines.append("  ⬇️ " + ", ".join(f"{r.ticker} {r.change_pct:+.1f}%" for r in down.itertuples()))
        lines.append("\nI 13F escono fino a 45 giorni dopo la fine del trimestre.")
        return "\n".join(lines)

    def cmd_avvisi(self, arg, chat_id):
        hist = REPORT_DIR / "storico_avvisi.csv"
        if not hist.exists():
            return "🔔 Nessun avviso ancora."
        h = pd.read_csv(hist).fillna("").tail(15).iloc[::-1]
        icon = {"alta": "🔴", "media": "🟠", "info": "🔵"}
        return "\n".join(["🔔 Ultimi avvisi:"] + [f"{icon.get(r.level, '')} {str(r.time)[5:16]} {r.text}" for r in h.itertuples()])

    def cmd_controlla(self, arg, chat_id):
        if not self._check_lock.acquire(blocking=False):
            return "⏳ Un controllo è già in corso, attendi."
        try:
            self.api.send(chat_id, "⏳ Controllo in corso… (circa un minuto)", keyboard=False)
            res = run_once(self.cfg, notify=False)
            self.snap = res["snapshot"]
            self.state["last_check"] = time.time()
            self._save_state()
            return res["message"]
        finally:
            self._check_lock.release()

    def cmd_crypto(self, arg, chat_id):
        try:
            import trend_crypto as tc
        except ImportError:
            return "🪙 La sezione crypto non è disponibile in questa installazione."
        self.api.send(chat_id, "⏳ Scansione crypto su Binance…", keyboard=False)
        radar, _ = tc.scan("binance", "USDT", 30, "1h")
        if radar.empty:
            return "🪙 Dati crypto non disponibili."
        lines = ["🪙 Crypto (Binance, timeframe 1h) – migliori:"]
        for r in radar.head(5).itertuples():
            lines.append(f"• {r.symbol} {r.score:+.0f} {r.signal} · {r.price:.6g}$ ({_pct(r.change_24h)} 24h)")
        lines.append("\nPeggiori:")
        for r in radar.tail(3).iloc[::-1].itertuples():
            lines.append(f"• {r.symbol} {r.score:+.0f} {r.signal}")
        return "\n".join(lines + ["", DISCLAIMER])

    def cmd_test(self, arg, chat_id):
        import previsioni

        journal = previsioni.load_journal()
        text = previsioni.summary_text(previsioni.compute_stats(journal))
        if journal:
            if not previsioni.EXCEL_FILE.exists():
                previsioni.write_excel(journal)
            self.api.send_document(chat_id, previsioni.EXCEL_FILE, "🧪 Test delle previsioni")
        else:
            text += "\nLe previsioni vengono registrate ad ogni controllo orario."
        return text

    def run_forward_test(self) -> None:
        """Passo orario del test delle previsioni crypto; avvisa quando un'operazione si chiude."""
        cfg = self.cfg
        if not cfg.get("test_previsioni", True):
            return
        try:
            import previsioni
        except ImportError:  # installazione senza la parte crypto
            return
        try:
            res = previsioni.run(cfg)
            log.info("Test previsioni: %d nuove, %d chiuse", len(res["new"]), len(res["closed"]))
            if res["closed"]:
                text = "\n".join([*(previsioni.closed_text(e) for e in res["closed"]), "",
                                  previsioni.summary_text(res["stats"])])
                notify_all(cfg, "🧪 Test previsioni", text, previsioni.closed_text(res["closed"][0]))
        except Exception:  # noqa: BLE001
            log.exception("Test previsioni fallito")

    def cmd_watchlist(self, arg, chat_id):
        wl = self.cfg["watchlist"]
        return f"👀 Watchlist ({len(wl)} titoli):\n" + ", ".join(wl)

    def cmd_aggiungi(self, arg, chat_id):
        add = [t.upper().replace(".", "-") for t in re.split(r"[\s,]+", arg) if t]
        if not add:
            return "Scrivi i ticker, es. /aggiungi TSLA AMD"
        cfg = self.cfg
        cfg["watchlist"] = cfg["watchlist"] + [t for t in add if t not in cfg["watchlist"]]
        save_config(cfg)
        return f"✅ Aggiunti: {', '.join(add)}. Saranno inclusi dal prossimo controllo."

    def cmd_rimuovi(self, arg, chat_id):
        rem = {t.upper().replace(".", "-") for t in re.split(r"[\s,]+", arg) if t}
        cfg = self.cfg
        cfg["watchlist"] = [t for t in cfg["watchlist"] if t not in rem]
        save_config(cfg)
        return f"🗑️ Rimossi: {', '.join(sorted(rem)) or 'nessuno'}."

    def cmd_stato(self, arg, chat_id):
        last = self.state.get("last_check")
        nxt = (last or time.time()) + self.check_every
        snap = self.snap
        lines = ["🤖 Bot attivo.",
                 f"Ultimo controllo: {time.strftime('%d/%m %H:%M', time.localtime(last)) if last else 'mai'}",
                 f"Prossimo controllo: {time.strftime('%d/%m %H:%M', time.localtime(nxt))}",
                 f"Watchlist: {len(self.cfg['watchlist'])} titoli"]
        if snap is not None:
            lines.append(f"Dati del {snap.fetched_at.tz_convert(None):%d/%m %H:%M} UTC")
            lines += ([f"⚠️ {e}" for e in snap.errors[:8]] or ["✅ Tutte le fonti raggiunte"])
        return "\n".join(lines)

    # -- ciclo principale ----------------------------------------------------- #
    def hourly_check(self) -> None:
        if not self._check_lock.acquire(blocking=False):
            return
        try:
            log.info("Controllo orario")
            res = run_once(self.cfg, notify=True)
            self.snap = res["snapshot"]
            log.info("Controllo: %d novità, %d inviate %s", len(res["alerts"]), len(res["pushed"]), res["status"])
        except Exception:  # noqa: BLE001
            log.exception("Controllo orario fallito")
        try:
            self.run_forward_test()
        except Exception:  # noqa: BLE001
            log.exception("Test previsioni fallito")
        finally:
            self.state["last_check"] = time.time()
            self._save_state()
            background.write_status(ultimo_controllo=time.strftime("%Y-%m-%dT%H:%M:%S"))
            self._check_lock.release()

    def poll(self, timeout: int = 25) -> int:
        updates = self.api.get_updates(self.state.get("offset", 0), timeout=timeout)
        for upd in updates:
            self.state["offset"] = upd["update_id"] + 1
            self._save_state()
            try:
                self.handle_update(upd)
            except Exception:  # noqa: BLE001
                log.exception("Messaggio non gestito")
        return len(updates)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()

        def stopped() -> bool:
            return stop.is_set() or background.stop_requested()

        def wait(seconds: float) -> None:  # attesa interrompibile dalla richiesta di arresto
            end = time.time() + seconds
            while not stopped() and time.time() < end:
                stop.wait(min(1.0, end - time.time()))

        commands_set = False
        backoff = 5
        while not stopped():
            due = time.time() - self.state.get("last_check", 0) >= self.check_every
            if due and not self._check_lock.locked():
                threading.Thread(target=self.hourly_check, daemon=True).start()
            if not self.cfg.get("telegram_token"):
                background.write_status(telegram="token mancante")
                wait(30)  # senza Telegram: solo controllo orario con notifiche desktop
                continue
            try:
                token = self.cfg["telegram_token"]
                if self.api is None or (self._token is not None and token != self._token):
                    self.api, commands_set = TelegramAPI(token), False  # token cambiato nelle impostazioni
                self._token = token
                if not commands_set:
                    self.api.set_commands()
                    commands_set = True
                self.poll()
                background.write_status(telegram="ok", ultimo_contatto=time.strftime("%Y-%m-%dT%H:%M:%S"))
                backoff = 5
            except Exception as e:  # noqa: BLE001 - rete assente, token errato, altro bot attivo…
                log.warning("Telegram: %s", e)
                background.write_status(telegram="errore", ultimo_errore=f"Telegram: {e}"[:300])
                wait(backoff)
                backoff = min(backoff * 2, 300)


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA_DIR / "bot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bot Telegram di Radar Ultimate")
    p.add_argument("--una-volta", action="store_true", help="rispondi ai messaggi in attesa ed esci")
    args = p.parse_args(argv)
    setup_logging()
    cfg = load_config()
    if args.una_volta:
        if not cfg.get("telegram_token"):
            print("Token Telegram non configurato.")
            return 1
        bot = RadarBot(TelegramAPI(cfg["telegram_token"]))
        n = bot.poll(timeout=0)
        print(f"{n} messaggi gestiti")
        return 0
    lock = background.acquire_instance_lock()
    if lock is None:
        print("Il bot è già in esecuzione.")
        log.info("Avvio ignorato: il bot è già in esecuzione")
        return 0
    background.clear_stop()
    background.STARTING_FILE.unlink(missing_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    background.write_status(stato="in esecuzione", pid=os.getpid(), avviato=started, telegram=None,
                            ultimo_contatto=None, ultimo_errore=None, ultimo_controllo=None)
    log.info("Bot avviato (pid %s)", os.getpid())
    api = TelegramAPI(cfg["telegram_token"]) if cfg.get("telegram_token") else None
    try:
        RadarBot(api).run_forever()
    except Exception as e:  # noqa: BLE001
        log.exception("Il bot si è fermato per un errore")
        background.write_status(stato="fermo per errore", ultimo_errore=f"{type(e).__name__}: {e}")
        return 1
    finally:
        lock.close()
    background.write_status(stato="fermo")
    log.info("Bot fermato")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
