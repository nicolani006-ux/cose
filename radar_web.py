"""Radar Web: l'app locale in stile sito (dashboard, segnali BUY massimo con preavviso, statistiche,
recap della giornata, calendario degli errori, bot Telegram).

    python radar_web.py                  apre http://127.0.0.1:8765 nel browser
    python radar_web.py --porta 9000     porta diversa
    python radar_web.py --no-browser     non aprire il browser
"""

import argparse
import logging


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Radar Web")
    p.add_argument("--porta", type=int, default=None)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from webapp.server import run

    run(port=args.porta, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
