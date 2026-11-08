"""Pagina Impostazioni & Bot: si apre subito, senza aspettare il caricamento dei dati."""

from __future__ import annotations

import os
import time

import streamlit as st

from stocks import background, notify
from stocks import secret
from stocks.config import DATA_DIR, UNIVERSE, load_config, pairing_code, save_config
from stocks.telegram_bot import TelegramAPI


@st.cache_data(ttl=300, show_spinner=False)
def bot_username(token: str) -> str | None:
    try:
        return TelegramAPI(token).call("getMe", http_timeout=10).get("username")
    except Exception:  # noqa: BLE001
        return None


def section_bot(cfg: dict) -> None:
    st.header("🤖 Bot Telegram")
    st.caption("Il bot ti manda gli avvisi sul telefono e risponde ai comandi: /compra, /titolo NVDA, /fed, "
               "/banche, /fondi, /notizie, /controlla, /crypto…")
    running = background.is_running()

    st.markdown("**1. Crea il bot** – su Telegram apri **@BotFather**, scrivi `/newbot`, scegli un nome "
                "e copia il *token* che ti dà (tipo `123456:ABC-…`).")
    with st.form("token"):
        token = st.text_input("Token del bot", cfg["telegram_token"], type="password")
        if st.form_submit_button("💾 Salva token", type="primary"):
            cfg["telegram_token"] = token.strip()
            save_config(cfg)
            st.rerun()

    if not cfg["telegram_token"]:
        return
    user = bot_username(cfg["telegram_token"])
    if user:
        st.success(f"Token valido: il tuo bot è **@{user}**")
    else:
        st.error("Token non valido o Telegram non raggiungibile.")

    running, starting = background.is_running(), background.starting()
    st.markdown("**2. Avvia il bot** – resta attivo in background anche ad app chiusa: risponde ai comandi "
                "e fa il controllo **ogni ora**.")
    a, b, _ = st.columns([1, 1, 2])
    if a.button("▶️ Avvia bot", disabled=running or starting, type="primary"):
        background.start()
        with st.spinner("Avvio del bot… (la prima volta può richiedere fino a un minuto)"):
            ok = background.wait_until_running()
        if ok:
            st.toast("✅ Bot avviato")
            st.rerun()
        st.error("Il bot non è partito. Ultime righe del registro (mandale a chi ti aiuta):")
        st.code(background.log_tail(25) or "(registro vuoto: il programma non è riuscito nemmeno a partire)",
                language=None)
    if b.button("⏹️ Ferma bot", disabled=not running):
        background.request_stop()
        with st.spinner("Arresto del bot… (fino a 30 secondi)"):
            end = time.time() + 45
            while background.is_running() and time.time() < end:
                time.sleep(1)
        st.rerun()
    live_status(running, starting, bool(cfg["telegram_chat_id"]))
    if os.name == "nt":
        auto = st.checkbox("Avvia il bot automaticamente all'accesso a Windows", background.autostart_enabled())
        if auto != background.autostart_enabled():
            background.set_autostart(auto)
            st.toast("Avvio automatico " + ("attivato" if auto else "disattivato"))
    else:
        st.caption(f"Avvio manuale: `{background.command_line()}`")
    with st.expander("📄 Registro del bot (bot.log)"):
        st.code(background.log_tail(40) or "(vuoto)", language=None)
        st.caption(f"File: `{background.LOG_FILE}`")

    st.markdown("**3. Collega il tuo account** – premi il pulsante qui sotto e poi **Avvia** su Telegram: "
                "da quel momento il bot risponde solo a te.")
    code = pairing_code(cfg)
    if user and not cfg["telegram_chat_id"]:
        st.link_button(f"🔗 Collega su Telegram (@{user})", f"https://t.me/{user}?start={code}", type="primary")
        st.caption(f"Il link contiene il tuo codice di collegamento **{code}** (in alternativa scrivi al bot "
                   f"`/start {code}`): senza il codice nessun altro può prendere il controllo del bot.")
    elif user:
        st.link_button(f"Apri @{user} su Telegram", f"https://t.me/{user}")
    if secret.available():
        st.caption("🔐 Il token è salvato cifrato (protezione di Windows): leggibile solo dal tuo utente su questo PC.")
    if cfg["telegram_chat_id"]:
        st.success(f"✅ Collegato alla chat {cfg['telegram_chat_id']}")
        x, y = st.columns(2)
        if x.button("🔔 Invia messaggio di prova"):
            st.write(notify.notify_all(cfg, "📊 Radar Ultimate – prova", "Le notifiche funzionano ✅",
                                       "Le notifiche funzionano ✅"))
        if y.button("Scollega chat"):
            cfg["telegram_chat_id"] = ""
            save_config(cfg)
            st.rerun()
    elif running:
        st.info("In attesa del tuo /start sul bot… questa pagina si aggiorna da sola.")
    elif starting:
        st.info("Attendi che il bot finisca di avviarsi (passo 2).")
    else:
        st.warning("Il bot è fermo: avvialo al passo 2, poi premi Avvia su Telegram.")
        if st.button("🔎 Ho già scritto al bot: collega la chat senza avviarlo",
                     help="Legge l'ultimo messaggio ricevuto dal bot e salva la tua chat."):
            try:
                ids = notify.find_chat_ids(cfg["telegram_token"], code=code)
            except Exception as e:  # noqa: BLE001
                st.error(f"Errore: {e}")
                ids = None
            if ids:
                cfg["telegram_chat_id"] = ids[-1].split(" ")[0]
                save_config(cfg)
                st.rerun()
            elif ids is not None:
                st.warning(f"Nessun messaggio con il codice trovato: scrivi al bot `/start {code}` e riprova.")


def _hm(iso: str | None) -> str:
    return iso[11:16] + (f" del {iso[8:10]}/{iso[5:7]}" if iso[:10] != time.strftime("%Y-%m-%d") else "") if iso else "mai"


@st.fragment(run_every=3)
def live_status(was_running: bool, was_starting: bool, was_linked: bool) -> None:
    """Stato del bot aggiornato ogni 3 secondi; ricarica la pagina quando cambia."""
    running, starting = background.is_running(), background.starting()
    linked = bool(load_config().get("telegram_chat_id"))
    if (running, starting, linked) != (was_running, was_starting, was_linked):
        st.rerun(scope="app")
    info = background.read_status()
    if running:
        tg = {"ok": "✅ collegato", "errore": "⚠️ errore", "token mancante": "⚠️ token mancante"}.get(
            info.get("telegram"), "in collegamento…")
        st.success(f"🟢 **Bot in esecuzione** dalle {_hm(info.get('avviato'))} · Telegram: {tg} · "
                   f"ultimo contatto {_hm(info.get('ultimo_contatto'))} · "
                   f"ultimo controllo orario {_hm(info.get('ultimo_controllo'))}")
        if info.get("telegram") == "errore" and info.get("ultimo_errore"):
            st.warning(f"Ultimo errore: {info['ultimo_errore']}")
    elif starting:
        st.info("🟡 **Bot in avvio…** può richiedere fino a un minuto.")
    else:
        st.error("🔴 **Bot fermo**")
        if info.get("stato") == "fermo per errore" and info.get("ultimo_errore"):
            st.caption(f"Si è fermato per un errore: {info['ultimo_errore']}")


def section_alerts(cfg: dict) -> None:
    st.header("🔔 Avvisi")
    with st.form("alerts"):
        desk = st.checkbox("Notifiche anche sul desktop di Windows", cfg["notifiche_desktop"])
        level = st.selectbox("Invia gli avvisi di livello almeno", ["info", "media", "alta"],
                             index=["info", "media", "alta"].index(cfg["livello_minimo_avviso"]),
                             help="alta: decisioni Fed/BCE, upgrade/downgrade delle 4 banche, segnali forti. "
                                  "media: anche discorsi Fed, notizie importanti, 13F, 8-K. info: tutto.")
        always = st.checkbox("Manda il riepilogo ad ogni controllo anche senza novità", cfg["invia_riepilogo_sempre"])
        q, m = st.columns(2)
        quiet = q.text_input("Ore di silenzio (es. 23-7, vuoto = mai)", cfg.get("ore_silenzio", ""),
                             help="In queste ore arrivano solo gli avvisi di livello alta; gli altri arrivano dopo.")
        mx = m.number_input("Avvisi elencati per messaggio", 1, 50, int(cfg.get("max_avvisi_messaggio", 10)))
        if st.form_submit_button("💾 Salva"):
            cfg.update(notifiche_desktop=desk, livello_minimo_avviso=level, invia_riepilogo_sempre=always,
                       ore_silenzio=quiet.strip(), max_avvisi_messaggio=int(mx))
            save_config(cfg)
            st.success("Salvato.")


def section_money(cfg: dict) -> None:
    st.header("💰 Capitale e rischio")
    st.caption("Con il capitale impostato, ogni piano BUY/SELL/STOP dice anche quanto comprare: la quantità più "
               "piccola tra perdita massima allo STOP, posizione massima, liquidità e ordine massimo "
               "(regole della skill trader-budget). Valori nella valuta dei prezzi: $ per le azioni, USDT per le crypto.")
    with st.form("money"):
        a, b = st.columns(2)
        cap = a.number_input("Capitale (0 = non calcolare)", min_value=0.0, value=float(cfg["capitale"]), step=100.0)
        loss = b.number_input("Perdita massima per operazione (allo STOP)", min_value=0.0,
                              value=float(cfg["rischio_max_operazione"]), step=5.0)
        c, d, e = st.columns(3)
        pos = c.number_input("Posizione massima (% del capitale)", 0.0, 100.0, float(cfg["posizione_max_pct"]), 5.0)
        buf = d.number_input("Riserva di liquidità (%)", 0.0, 100.0, float(cfg["riserva_cassa_pct"]), 5.0)
        mx = e.number_input("Ordine massimo (0 = nessun limite)", min_value=0.0, value=float(cfg["ordine_max"]),
                            step=100.0)
        if st.form_submit_button("💾 Salva"):
            cfg.update(capitale=cap, rischio_max_operazione=loss, posizione_max_pct=pos, riserva_cassa_pct=buf,
                       ordine_max=mx)
            save_config(cfg)
            st.success("Salvato.")


def section_sources(cfg: dict) -> None:
    st.header("📡 Fonti e watchlist")
    with st.form("sources"):
        ua = st.text_input("Nome ed email per la SEC (obbligatori per scaricare i dati EDGAR)", cfg["sec_user_agent"],
                           placeholder="Mario Rossi mario.rossi@email.it")
        c13, c8 = st.columns(2)
        f13 = c13.checkbox("Analizza i 13F di BlackRock / Vanguard / State Street", cfg["controlla_13f"])
        f8 = c8.checkbox("Controlla le comunicazioni 8-K", cfg["controlla_8k"])
        fc = st.checkbox("Segui le operazioni in borsa dei deputati USA (moduli del House Clerk)",
                         cfg["controlla_congresso"])
        wl = st.text_area("Watchlist (ticker separati da virgola)", ", ".join(cfg["watchlist"]), height=120,
                          help="Titoli USA come su Yahoo Finance (es. BRK-B). Nomi e settori già noti per: "
                               + ", ".join(UNIVERSE))
        if st.form_submit_button("💾 Salva"):
            cfg.update(sec_user_agent=ua.strip(), controlla_13f=f13, controlla_8k=f8, controlla_congresso=fc,
                       watchlist=[t.strip().upper() for t in wl.replace("\n", ",").split(",") if t.strip()])
            save_config(cfg)
            st.cache_resource.clear()
            st.success("Salvato. I dati verranno ricaricati.")


def main() -> None:
    st.set_page_config(page_title="Impostazioni", page_icon="⚙️", layout="wide")
    st.title("⚙️ Impostazioni & Bot")
    cfg = load_config()
    section_bot(cfg)
    st.divider()
    section_alerts(cfg)
    st.divider()
    section_money(cfg)
    st.divider()
    section_sources(cfg)
    st.caption(f"Impostazioni, report e log in: `{DATA_DIR}`")


if __name__ == "__main__":
    main()
