"""Avvia Crypto Radar Ultimate come applicazione desktop (finestra nativa).

Questo è il file d'ingresso usato da PyInstaller per creare l'.exe (Windows)
o l'.app (macOS). Da sorgente:  python launcher.py

Senza finestra:  --monitor  un controllo delle azioni con invio degli avvisi
                 --bot      bot Telegram + controllo ogni ora (avviato dalle Impostazioni)
                 --excel    solo test delle previsioni crypto + file Excel
                 --versione stampa la versione
                 --self-test verifica che tutti i moduli dell'eseguibile si carichino (usato dalla CI)
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


SELF_TEST_MODULES = [
    "app", "nav", "trend_crypto", "stock_radar", "settings_page", "test_page", "previsioni", "indicators", "charts",
    "ui_common", "crypto_core", "excel_previsioni", "version", "stocks.config", "stocks.sources", "stocks.analysis",
    "stocks.monitor", "stocks.notify", "stocks.background", "stocks.telegram_bot", "stocks.skills", "stocks.congress",
    "stocks.fundamentals", "stocks.secret", "stocks.updates", "ccxt", "feedparser", "openpyxl", "altair", "yfinance",
    "pypdf", "plyer", "streamlit", "streamlit_desktop_app",
]


def self_test() -> int:
    """Importa tutti i moduli e carica gli script delle skill: se nell'eseguibile manca qualcosa
    (hiddenimports di PyInstaller), fallisce qui invece che all'apertura di una pagina."""
    import importlib
    import traceback

    errors = []
    for name in SELF_TEST_MODULES:
        if name == "app":
            continue  # app.py viene eseguito da Streamlit, non importato
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001
            errors.append(f"{name}: {traceback.format_exc(limit=1)}")
    try:
        from stocks.skills import load_skill

        load_skill("trader-pelosi", "ptr")
        load_skill("trader-analyst", "facts")
    except Exception:  # noqa: BLE001
        errors.append(f"skill: {traceback.format_exc(limit=1)}")
    if not os.path.exists(get_script_path()):
        errors.append(f"manca {get_script_path()}")
    report = "OK" if not errors else "\n".join(errors)
    try:
        from stocks.config import DATA_DIR

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "self_test.txt").write_text(report, encoding="utf-8")  # senza console non si vedrebbe
    except OSError:
        pass
    print(report)
    return 0 if not errors else 1


def get_script_path() -> str:
    if hasattr(sys, "_MEIPASS"):  # eseguibile creato con PyInstaller
        return os.path.join(sys._MEIPASS, SCRIPT)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), SCRIPT)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.dirname(get_script_path()))
    if "--versione" in sys.argv:
        from version import VERSION

        print(VERSION)
        sys.exit(0)
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--monitor" in sys.argv:  # solo il controllo, senza finestra
        from stocks.monitor import main as monitor_main

        sys.exit(monitor_main([a for a in sys.argv[1:] if a != "--monitor"]))
    if "--excel" in sys.argv:  # solo test delle previsioni + file Excel
        from excel_previsioni import main as excel_main

        sys.exit(excel_main([a for a in sys.argv[1:] if a != "--excel"]))
    if "--bot" in sys.argv:  # bot Telegram + controllo orario in background
        try:
            from stocks.telegram_bot import main as bot_main

            sys.exit(bot_main([a for a in sys.argv[1:] if a != "--bot"]))
        except Exception:  # registra anche gli errori di avvio (senza finestra non si vedrebbero)
            import traceback

            from stocks.background import log_crash

            log_crash(traceback.format_exc())
            sys.exit(1)

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
