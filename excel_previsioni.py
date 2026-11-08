"""Aggiorna il test delle previsioni crypto e il file Excel, senza aprire l'app.

    python excel_previsioni.py                     un aggiornamento (scarica i prezzi da Binance)
    python excel_previsioni.py --loop 60           ripete ogni 60 minuti
    python excel_previsioni.py --solo-excel        rigenera l'Excel dai dati salvati, senza internet
    python excel_previsioni.py --cartella DIR      dove tenere dati ed Excel (predefinita: RadarUltimate)
    python excel_previsioni.py --avvisa            avvisa su Telegram quando un'operazione si chiude
                                                   (usato dal controllo su GitHub Actions)

Il file è <cartella>/report/test_previsioni.xlsx: colonna A crypto, B probabilità di rialzo ipotizzata,
C prezzo BUY → SELL ipotizzato, D margine ipotizzato, E guadagno effettivo, poi dettagli e statistiche.
Servono solo: pip install -r requirements-previsioni.txt
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Test delle previsioni crypto → Excel")
    p.add_argument("--cartella", help="cartella dei dati (default: RadarUltimate nella cartella utente)")
    p.add_argument("--loop", type=int, metavar="MINUTI", help="ripeti ogni N minuti")
    p.add_argument("--solo-excel", action="store_true", help="solo rigenera l'Excel dai dati già salvati")
    p.add_argument("--avvisa", action="store_true", help="avvisa (Telegram/desktop) quando un'operazione si chiude")
    args = p.parse_args(argv)
    if args.cartella:
        os.environ["RADAR_HOME"] = os.path.abspath(args.cartella)

    import previsioni as pv  # dopo RADAR_HOME: i percorsi dipendono dalla cartella scelta
    from stocks.config import load_config

    while True:
        if args.solo_excel:
            journal = pv.load_journal()
            pv.write_excel(journal)
            res = {"new": [], "closed": [], "stats": pv.compute_stats(journal), "error": None}
        else:
            res = pv.run(load_config())
        print(f"[{time.strftime('%Y-%m-%d %H:%M')}] {len(res['new'])} nuove previsioni, "
              f"{len(res['closed'])} operazioni chiuse")
        for e in res["closed"]:
            print("  " + pv.closed_text(e))
        if args.avvisa and res["closed"]:
            from stocks.notify import notify_all

            text = "\n".join([*(pv.closed_text(e) for e in res["closed"]), "", pv.summary_text(res["stats"])])
            print(notify_all(load_config(), "🧪 Test previsioni", text, pv.closed_text(res["closed"][0])))
        if res["error"]:
            print(f"⚠️ Exchange non raggiungibile ({res['error']}): Excel rigenerato con i dati già salvati.")
        print(pv.summary_text(res["stats"]))
        print(f"File Excel: {pv.EXCEL_FILE}", flush=True)
        if not args.loop:
            return 2 if res["error"] else 0
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    raise SystemExit(main())
