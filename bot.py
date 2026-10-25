"""Bot Telegram di Radar Ultimate: comandi + controllo orario con avvisi.

    python bot.py              resta acceso (usato anche dall'avvio automatico)
    python bot.py --una-volta  risponde ai messaggi in attesa ed esce
"""

if __name__ == "__main__":
    try:
        from stocks.telegram_bot import main

        raise SystemExit(main())
    except Exception:  # con pythonw non c'è console: l'errore finisce nel registro bot.log
        import traceback

        from stocks.background import log_crash

        log_crash(traceback.format_exc())
        raise SystemExit(1)
