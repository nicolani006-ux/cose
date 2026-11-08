"""Operazioni in borsa dei membri del Congresso USA (STOCK Act, Camera dei Rappresentanti).

Usa il lettore della skill trader-pelosi (day-trader-skill di J. Schincariol, licenza MIT):
indice annuale dei moduli del House Clerk e lettura dei PDF dei Periodic Transaction Report.
Per i PDF usa `pdftotext` se c'è, altrimenti pypdf (puro Python, incluso nell'app per Windows)
con una conversione del testo nel formato che il lettore si aspetta.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

import pandas as pd

from stocks.skills import load_skill

def ptr():
    """Il modulo ptr.py della skill trader-pelosi."""
    return load_skill("trader-pelosi", "ptr")


# --------------------------------------------------------------------------- #
# Testo dei PDF
# --------------------------------------------------------------------------- #

_TXN_TAIL = re.compile(r"(?:^|\s)(S \(partial\)|P|S|E)\s+\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\$")
_LABEL = re.compile(r"^(F(?:ILING)?\s+S(?:TATUS)?|S(?:UBHOLDING)?\s+O(?:F)?|D(?:ESCRIPTION)?|C(?:OMMENTS)?)\s*:", re.I)
_HEADER = re.compile(r"^(ID\s+Owner|Type$|Date|Amount|Gains|\$200\?|\* For the complete|Filing ID|Name:|Status:|"
                     r"State/District:|Clerk of the House)", re.I)


_AMOUNT_TAIL = re.compile(r"^\$[\d,]+$")
_NOISE = re.compile(r"^(?:[A-Z](?:\s+|$))+$")          # resti dei titoli in maiuscoletto: "P T R", "F I"
_OWNER_START = re.compile(r"^(SP|JT|DC)\s")


def pypdf_text_to_layout(text: str) -> str:
    """Il testo "semplice" di pypdf mette l'attività su più righe e la transazione su una riga a sé
    (a volte con la fine del nome o dell'importo dopo, anche a pagina nuova): ricostruisce una riga
    per transazione, come `pdftotext -layout`, che è il formato atteso dal lettore della skill."""
    out: list[str] = []
    pending: list[str] = []      # righe non ancora attribuite
    last_txn = None              # (indice in out, posizione del tipo di transazione) dell'ultima transazione

    def attach(lines_):
        """Le righe in sospeso erano il seguito dell'ultima transazione (nome o importo)."""
        nonlocal last_txn
        i, pos = last_txn
        for ln in lines_:
            if _AMOUNT_TAIL.match(ln) and out[i].endswith("-"):
                out[i] += " " + ln
            else:
                out[i] = out[i][:pos].rstrip() + " " + ln + " " + out[i][pos:]
                pos += len(ln) + 1
        last_txn = (i, pos)

    # pypdf scrive \x00 al posto delle lettere in maiuscoletto ("F\x00\x00 S\x00: New")
    lines = [re.sub(r"\s+", " ", ln.replace("\x00", " ")).strip() for ln in text.splitlines()]
    for s in (ln for ln in lines if ln):
        if _NOISE.match(s):
            continue
        if _HEADER.match(s):
            out.append(s)                     # intestazione (anche di pagina): non chiude la transazione
            continue
        m = _TXN_TAIL.search(s)
        if m:
            asset = pending
            if last_txn and pending:
                # se una riga in sospeso apre una nuova attività ("SP ...", "JT ..."), quelle prima erano il seguito
                starts = [k for k, ln in enumerate(pending) if _OWNER_START.match(ln)]
                first = starts[-1] if starts else 0
                if first:
                    attach(pending[:first])
                asset = pending[first:]
            row = " ".join(asset + [s])
            out.append(row)
            last_txn = (len(out) - 1, len(row) - len(s) + m.start(1))
            pending = []
        elif _LABEL.match(s):
            if pending and last_txn:
                attach(pending)
            else:
                out.extend(pending)
            out.append(s)
            pending, last_txn = [], None
        else:
            pending.append(s)
    if pending:
        if last_txn:
            attach(pending)
        else:
            out.extend(pending)
    return "\n".join(out)


def pdf_text(path: str) -> str | None:
    if shutil.which("pdftotext"):
        txt = ptr().pdf_to_text(str(path))
        if txt:
            return txt
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        raw = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 - PDF danneggiato o scansionato
        return None
    return pypdf_text_to_layout(raw)


# --------------------------------------------------------------------------- #
# Moduli e operazioni
# --------------------------------------------------------------------------- #

def recent_filings(days: int = 30, members: list[str] | None = None, today: dt.date | None = None,
                   index_rows: list[dict] | None = None) -> list[dict]:
    """Periodic Transaction Report depositati negli ultimi `days` giorni (più recenti prima)."""
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=days)).isoformat()
    if index_rows is None:
        index_rows = ptr().load_index(today.year)
        if today.month == 1:
            try:
                index_rows += ptr().load_index(today.year - 1)
            except Exception:  # noqa: BLE001 - indice dell'anno prima non disponibile
                pass
    rows = ptr().select(index_rows, members=members or None, kind="P", since=since)
    return sorted(rows, key=lambda r: (r["filed"] or "", r["doc_id"]), reverse=True)


def filing_trades(row: dict, cache_dir: Path) -> dict:
    """Scarica (una volta sola) e legge il PDF di un modulo: righe con ticker, acquisto/vendita, importi."""
    P = ptr()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc = str(row["doc_id"])
    if not re.fullmatch(r"\d{1,12}", doc):
        raise ValueError(f"DocID non valido: {doc!r}")
    path = cache_dir / f"{doc}.pdf"
    if not path.exists():
        data = P.download(P.PTR_URL % (row["year"], doc))
        if not data.startswith(b"%PDF"):
            raise ValueError(f"il modulo {doc} non è un PDF")
        path.write_bytes(data)
    txt = pdf_text(str(path))
    report = P.parse_ptr_text(txt, row.get("filed")) if txt else {"rows": []}
    report.update(name=row.get("name"), district=row.get("district"), doc_id=doc, filed=row.get("filed"),
                  url=P.PTR_URL % (row["year"], doc), readable=bool(txt))
    return report


CONGRESS_COLUMNS = ["member", "district", "filed", "doc_id", "url", "owner", "ticker", "asset", "type", "type_name",
                    "date", "amount", "amount_low", "amount_high", "days_to_disclose", "late"]


def collect_trades(cache_dir: Path, days: int = 45, max_filings: int = 60, pause: float = 0.2,
                   errors: list | None = None, today: dt.date | None = None) -> pd.DataFrame:
    """Operazioni dichiarate dai deputati negli ultimi `days` giorni (una riga per operazione).
    I PDF già letti restano in `cache_dir`, quindi ad ogni controllo si scaricano solo i nuovi."""
    import time

    rows = []
    for f in recent_filings(days=days, today=today)[:max_filings]:
        cached = (Path(cache_dir) / f"{f['doc_id']}.pdf").exists()
        try:
            rep = filing_trades(f, cache_dir)
        except Exception as e:  # noqa: BLE001 - un modulo illeggibile non blocca gli altri
            if errors is not None:
                errors.append(f"Congresso {f['doc_id']}: {type(e).__name__}")
            continue
        for r in rep["rows"]:
            rows.append({
                "member": (f.get("name") or "").replace("Hon. ", ""), "district": f.get("district"),
                "filed": f.get("filed"), "doc_id": f["doc_id"], "url": rep.get("url"),
                **{k: r.get(k) for k in ("owner", "ticker", "asset", "type", "type_name", "date", "amount",
                                         "amount_low", "amount_high", "days_to_disclose", "late")},
            })
        if not cached:
            time.sleep(pause)
    return pd.DataFrame(rows, columns=CONGRESS_COLUMNS)


def describe_trade(r) -> str:
    who = {"SP": " (coniuge)", "JT": " (conto congiunto)", "DC": " (figlio a carico)"}.get(r.get("owner") or "", "")
    verb = {"P": "compra", "S": "vende", "S (partial)": "vende in parte", "E": "scambia"}.get(r.get("type"), r.get("type"))
    what = r.get("ticker") or (r.get("asset") or "")[:40]
    return (f"{r.get('member')}{who} {verb} {what}: {r.get('amount')}, operazione del {r.get('date')}, "
            f"dichiarata il {r.get('filed')}")
