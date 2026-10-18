"""Controllo orario di azioni, banche d'affari, Fed e SEC con invio degli avvisi.

    python monitor.py                 un controllo e poi esce
    python monitor.py --loop 60       controlla ogni 60 minuti
    python monitor.py --test-notifica prova Telegram / notifiche desktop
"""

from stocks.monitor import main

if __name__ == "__main__":
    raise SystemExit(main())
