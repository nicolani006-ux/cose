"""Pagina Impostazioni & Bot: si apre subito, senza aspettare il caricamento dei dati."""

from __future__ import annotations

import os

import streamlit as st

from stocks import background, notify
from stocks.config import DATA_DIR, UNIVERSE, load_config, save_config
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

    st.markdown("**2. Avvia il bot** – resta attivo in background anche ad app chiusa: risponde ai comandi "
                "e fa il controllo **ogni ora**.")
    a, b, c = st.columns([1, 1, 2])
    if a.button("▶️ Avvia bot", disabled=running, type="primary"):
        background.start()
        st.toast("Bot in avvio…")
        st.rerun()
    if b.button("⏹️ Ferma bot", disabled=not running):
        background.request_stop()
        st.toast("Il bot si fermerà entro 30 secondi.")
    c.write("Stato: " + ("🟢 **in esecuzione**" if running else "🔴 **fermo**"))
    if os.name == "nt":
        auto = st.checkbox("Avvia il bot automaticamente all'accesso a Windows", background.autostart_enabled())
        if auto != background.autostart_enabled():
            background.set_autostart(auto)
            st.toast("Avvio automatico " + ("attivato" if auto else "disattivato"))
    else:
        st.caption(f"Avvio manuale: `{background.command_line()}`")

    st.markdown("**3. Collega il tuo account** – apri il bot e premi **Avvia** (o scrivi `/start`): "
                "da quel momento risponde solo a te.")
    if user:
        st.link_button(f"Apri @{user} su Telegram", f"https://t.me/{user}")
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
        st.info("In attesa del tuo /start sul bot…")
        st.button("🔄 Aggiorna stato")
    else:
        st.warning("Avvia prima il bot (passo 2), poi premi Avvia su Telegram.")


def section_alerts(cfg: dict) -> None:
    st.header("🔔 Avvisi")
    with st.form("alerts"):
        desk = st.checkbox("Notifiche anche sul desktop di Windows", cfg["notifiche_desktop"])
        level = st.selectbox("Invia gli avvisi di livello almeno", ["info", "media", "alta"],
                             index=["info", "media", "alta"].index(cfg["livello_minimo_avviso"]),
                             help="alta: decisioni Fed/BCE, upgrade/downgrade delle 4 banche, segnali forti. "
                                  "media: anche discorsi Fed, notizie importanti, 13F, 8-K. info: tutto.")
        always = st.checkbox("Manda il riepilogo ad ogni controllo anche senza novità", cfg["invia_riepilogo_sempre"])
        if st.form_submit_button("💾 Salva"):
            cfg.update(notifiche_desktop=desk, livello_minimo_avviso=level, invia_riepilogo_sempre=always)
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
        wl = st.text_area("Watchlist (ticker separati da virgola)", ", ".join(cfg["watchlist"]), height=120,
                          help="Titoli USA come su Yahoo Finance (es. BRK-B). Nomi e settori già noti per: "
                               + ", ".join(UNIVERSE))
        if st.form_submit_button("💾 Salva"):
            cfg.update(sec_user_agent=ua.strip(), controlla_13f=f13, controlla_8k=f8,
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
    section_sources(cfg)
    st.caption(f"Impostazioni, report e log in: `{DATA_DIR}`")


if __name__ == "__main__":
    main()
