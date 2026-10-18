"""Avvia Crypto Radar Ultimate come applicazione desktop (finestra nativa).

Questo è il file d'ingresso usato da PyInstaller per creare l'.exe (Windows)
o l'.app (macOS). Da sorgente:  python launcher.py

Con l'opzione --monitor esegue solo il controllo delle azioni e invia gli avvisi,
senza aprire la finestra (usato dal controllo orario di Windows).
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
    if "--monitor" in sys.argv:
        sys.path.insert(0, os.path.dirname(get_script_path()))
        from stocks.monitor import main as monitor_main

        sys.exit(monitor_main([a for a in sys.argv[1:] if a != "--monitor"]))

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
