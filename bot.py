"""Bot Telegram di Radar Ultimate: comandi + controllo orario con avvisi.

    python bot.py              resta acceso (usato anche dall'avvio automatico)
    python bot.py --una-volta  risponde ai messaggi in attesa ed esce
"""

from stocks.telegram_bot import main

if __name__ == "__main__":
    raise SystemExit(main())
