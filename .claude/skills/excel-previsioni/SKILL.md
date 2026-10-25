---
name: excel-previsioni
description: Aggiorna il test delle previsioni crypto sui prezzi reali e produce il file Excel test_previsioni.xlsx (A crypto, B probabilità di rialzo ipotizzata, C prezzo BUY → SELL ipotizzato, D margine ipotizzato, E guadagno effettivo, più fogli di statistiche). Usala quando l'utente chiede l'Excel del test, i risultati delle previsioni, "/test" o "aggiorna l'excel".
---

# Excel del test delle previsioni

Fai **solo** questo: aggiorna le previsioni e genera l'Excel. Non modificare il codice del progetto.

## 1. Scegli la cartella dei dati

- Se esiste `~/RadarUltimate/previsioni.json` (Windows: `%USERPROFILE%\RadarUltimate\previsioni.json`), il bot
  dell'app sta già facendo il test su questo computer: usa quella cartella, cioè **non** passare `--cartella`.
- Altrimenti (per esempio in una sessione cloud) usa la cartella del repository `dati_previsioni`
  (`--cartella dati_previsioni`), così lo storico resta salvato con git tra una sessione e l'altra.

## 2. Dipendenze

Se `python -c "import ccxt, pandas, numpy, openpyxl"` fallisce, installa:
`pip install -q -r requirements-previsioni.txt`

## 3. Esegui

```
python excel_previsioni.py [--cartella dati_previsioni]
```

Lo script:
1. segue le previsioni già registrate sulle candele reali (BUY limite valido 5 candele, poi SELL / STOP /
   scadenza dopo 48 candele: stesse regole della statistica storica dell'app);
2. registra una nuova previsione per ogni crypto con segnale d'acquisto (Binance, USDT, prime 30, 1h;
   modificabile in `config.json` con `test_exchange`, `test_quote`, `test_coins`, `test_timeframe`);
3. riscrive `<cartella>/report/test_previsioni.xlsx` e stampa un riepilogo.

Codici d'uscita: `0` tutto ok; `2` exchange non raggiungibile. Con `2` l'Excel viene comunque rigenerato
con i dati già salvati, ma **non ci sono prezzi nuovi**: dillo chiaramente all'utente e non inventare né
simulare risultati. I prezzi arrivano da `data-api.binance.vision` (server ufficiale di Binance per i dati
pubblici, funziona anche dai server cloud negli USA) con ripiego su `api.binance.com` (bloccato negli USA):
in una sessione cloud almeno `data-api.binance.vision` va consentito nelle impostazioni di rete dell'ambiente.

Per rigenerare il file senza internet: `python excel_previsioni.py --solo-excel [--cartella ...]`.

## 4. Rispondi all'utente (in italiano)

- Riporta il riepilogo stampato: previsioni, operazioni chiuse, successo reale contro probabilità
  ipotizzata, margine ipotizzato contro guadagno reale, risultato con 100 € per operazione.
- Elenca le operazioni appena chiuse, se ce ne sono.
- Consegna il file Excel (inviandolo all'utente se hai uno strumento per farlo, altrimenti indica il percorso).
- Ricorda in una riga che con poche operazioni chiuse le statistiche non sono ancora affidabili.

## 5. Salva lo storico (solo con `--cartella dati_previsioni`)

Se la cartella è dentro un repository git, fai commit di `dati_previsioni/` con il messaggio
`Aggiorna test previsioni` e push sul branch corrente, così il prossimo aggiornamento riparte da qui.
