"""Avvia Crypto Radar Ultimate come applicazione desktop (finestra nativa).

Questo è il file d'ingresso usato da PyInstaller per creare l'.exe (Windows)
o l'.app (macOS). Da sorgente:  python launcher.py

Senza finestra:  --monitor  un controllo delle azioni con invio degli avvisi
                 --bot      bot Telegram + controllo ogni ora (avviato dalle Impostazioni)
                 --excel    solo test delle previsioni crypto + file Excel
"""

import multiprocessing
import os
import sys

# Nelle build "--windowed" stdout/stderr non esistono: Streamlit ci scrive e
# andrebbe in crash. Li reindirizziamo su un file nulla.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

APP_TITLE = "Crypto Radar Ultimate – Crypto & Azioni"
SCRIPT = "app.py"


def get_script_path() -> str:
    if hasattr(sys, "_MEIPASS"):  # eseguibile creato con PyInstaller
        return os.path.join(sys._MEIPASS, SCRIPT)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), SCRIPT)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.dirname(get_script_path()))
    if "--monitor" in sys.argv:  # solo il controllo, senza finestra
        from stocks.monitor import main as monitor_main

        sys.exit(monitor_main([a for a in sys.argv[1:] if a != "--monitor"]))
    if "--excel" in sys.argv:  # solo test delle previsioni + file Excel
        from excel_previsioni import main as excel_main

        sys.exit(excel_main([a for a in sys.argv[1:] if a != "--excel"]))
    if "--bot" in sys.argv:  # bot Telegram + controllo orario in background
        from stocks.telegram_bot import main as bot_main

        sys.exit(bot_main([a for a in sys.argv[1:] if a != "--bot"]))

    from streamlit_desktop_app import start_desktop_app

    start_desktop_app(
        get_script_path(),
        title=APP_TITLE,
        width=1400,
        height=900,
        options={
            "theme.base": "dark",
            "browser.gatherUsageStats": "false",
            "client.toolbarMode": "minimal",
        },
    )
