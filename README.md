# 📡 Crypto Radar Ultimate

App desktop (Streamlit + finestra nativa) con due sezioni:

- **🪙 Crypto** – scanner di trend per criptovalute
- **📈 Azioni & Istituzioni** – classifica delle azioni USA, giudizi delle grandi banche d'affari,
  Fed/BCE, SEC (8-K e 13F di BlackRock, Vanguard, State Street) e **avvisi automatici ogni ora**

## 🪙 Crypto

- **Radar**: scarica le coin con più volume dall'exchange scelto (Binance, Bybit, OKX,
  KuCoin, Gate.io, MEXC, Kraken) ed assegna a ciascuna un **punteggio di trend da -100 a +100**
  basato su EMA 20/50/200, MACD, RSI, volume relativo e breakout dei massimi/minimi.
  Esclude in automatico stablecoin e token a leva.
- **Allerte**: RSI ipercomprato/ipervenduto, golden/death cross, incroci MACD,
  picchi di volume, breakout.
- **Analisi**: grafico a candele con medie mobili, RSI, MACD, supporti/resistenze e Bollinger.
- **Notizie**: feed RSS da Cointelegraph, CoinDesk, Decrypt, Bitcoin Magazine.
- **Fear & Greed Index** (alternative.me).
- **Esporta** in Excel o CSV.
- Aggiornamento automatico opzionale.

Usa solo dati pubblici: **non servono chiavi API** e l'app non fa operazioni di trading.
> ⚠️ I segnali sono indicativi e non costituiscono consiglio finanziario.

## 📈 Azioni & Istituzioni

Per ogni titolo della watchlist (55 grandi aziende USA, modificabile) calcola un **punteggio da -100 a +100**
e indica i **migliori candidati all'acquisto** e quelli da evitare, spiegando sempre il perché:

| Peso | Componente | Fonte |
|---|---|---|
| 35% | Analisi tecnica (EMA 20/50/200, MACD, RSI, volumi, breakout) | Yahoo Finance |
| 30% | Upgrade/downgrade e target price di **Goldman Sachs, J.P. Morgan, Morgan Stanley, Bank of America** (peso pieno) e Citi, Wells Fargo, UBS, Barclays, Deutsche Bank (peso ridotto) | Yahoo Finance |
| 15% | Sentiment delle notizie che citano il titolo; comunicazioni SEC 8-K negative | Google News, SEC EDGAR |
| 10% | Azioni comprate/vendute da **BlackRock, Vanguard, State Street** (confronto ultimi due 13F) | SEC EDGAR |
| 10% | Tono della **Federal Reserve** (tassi giù/su) e suo effetto sul settore | federalreserve.gov |

### Fonti controllate

- **Comunicati ufficiali**: Federal Reserve (comunicati, discorsi, audizioni), BCE, SEC
- **Notizie** (Google News, che raccoglie i titoli di Reuters, Bloomberg, WSJ/Dow Jones, CNBC, FT…) su:
  Goldman Sachs, J.P. Morgan, Morgan Stanley, Bank of America, Federal Reserve, BlackRock, Vanguard,
  State Street, Casa Bianca / Tesoro USA, BCE / Commissione Europea, Wall Street
- **SEC EDGAR**: moduli 8-K (eventi rilevanti delle aziende) e 13F (portafogli dei grandi gestori)

> **Bloomberg Terminal, Refinitiv Eikon, Dow Jones Newswires, Koyfin, TradingView** non sono collegati:
> sono servizi a pagamento con licenza (Bloomberg/Refinitiv/Dow Jones richiedono abbonamento e credenziali
> proprie, Koyfin non ha API pubbliche, TradingView non fornisce dati a programmi esterni).
> I loro titoli principali arrivano comunque tramite Google News. Se hai un abbonamento con accesso API
> si può aggiungere come nuova fonte in `stocks/sources.py`.
>
> I giudizi delle banche sono quelli pubblici (upgrade/downgrade); i report completi sono riservati ai clienti.
> I 13F escono fino a 45 giorni dopo la fine del trimestre: mostrano cosa i gestori *hanno* comprato.

### Avvisi

Ad ogni controllo l'app confronta i dati con il controllo precedente e avvisa **solo sulle novità**:

- 🔴 **alta**: decisioni/comunicati di politica monetaria Fed/BCE, upgrade/downgrade delle 4 banche,
  titoli che diventano "Acquisto forte" o "Vendita forte", 8-K negativi (bilanci non affidabili, bancarotta…)
- 🟠 **media**: discorsi della Fed, notizie con sentiment forte su titoli della watchlist,
  nuovi 13F, 8-K rilevanti (risultati, acquisizioni, cambio dirigenti…)
- 🔵 **info**: tutto il resto (visibile nell'app, non inviato)

Ogni messaggio termina con la classifica aggiornata dei **migliori candidati all'acquisto**.

**Canali**: notifica di Windows e/o **Telegram** (arriva sul telefono). Per Telegram:
1. su Telegram scrivi `/newbot` a **@BotFather** e copia il token;
2. scrivi un messaggio qualsiasi al tuo nuovo bot;
3. nell'app: *Azioni & Istituzioni → ⚙️ Impostazioni*, incolla il token, premi **Trova il mio Chat ID**,
   copia il numero nel campo Chat ID e salva; poi **Invia notifica di prova**.

Nelle impostazioni inserisci anche **nome ed email** per la SEC (EDGAR li richiede a chi scarica i dati).

### Controllo ogni ora

- **Con il PC acceso** (consigliato): *⚙️ Impostazioni → Attiva controllo orario*. Crea un'attività
  nell'Utilità di pianificazione di Windows che ogni ora esegue `Crypto Radar Ultimate.exe --monitor`
  (oppure `monitor.py` da sorgente), anche ad app chiusa.
- **Da riga di comando**: `python monitor.py` (un controllo), `python monitor.py --loop 60` (ogni ora),
  `python monitor.py --test-notifica`.
- **Anche a PC spento** (GitHub Actions, workflow `monitor.yml`): nel repository su GitHub vai in
  *Settings → Secrets and variables → Actions* e crea i secret `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`,
  `SEC_USER_AGENT` e la variabile `MONITOR_ATTIVO` = `true` (facoltativa `WATCHLIST`, es. `AAPL,NVDA,JPM`).
  Funziona solo dal branch principale (`main`). Ogni controllo dura 1–2 minuti: su un repository privato
  24 controlli al giorno usano gran parte dei 2000 minuti gratuiti mensili di GitHub.

Dati, impostazioni e report sono nella cartella `RadarUltimate` dentro la tua cartella utente
(`C:\Users\<nome>\RadarUltimate`): `config.json`, `report/ultimo_report.txt`, `report/storico_avvisi.csv`.

> ⚠️ Il punteggio è un sistema automatico basato su regole: non prevede il futuro e non è consulenza
> finanziaria. Usalo come punto di partenza per le tue valutazioni.

## Scaricare l'app già pronta (Windows / Mac)

Ad ogni push GitHub Actions compila l'app:
**Actions → Build → ultima esecuzione → Artifacts**

- `Crypto-Radar-Ultimate-Windows` → contiene `Crypto Radar Ultimate.exe`
- `Crypto-Radar-Ultimate-macOS` → contiene `Crypto Radar Ultimate.dmg`

Su Windows, al primo avvio SmartScreen può mostrare "PC protetto da Windows":
clicca *Ulteriori informazioni → Esegui comunque* (l'eseguibile non è firmato).
L'avvio impiega qualche secondo perché l'exe si scompatta.

## Avviare da sorgente (Windows)

1. Installa [Python 3.12](https://www.python.org/downloads/) (spunta *Add Python to PATH*).
2. Doppio clic su `run_windows.bat`.

Oppure nel browser, senza finestra desktop:

```bat
pip install -r requirements.txt
streamlit run app.py
```

## Creare l'eseguibile in locale

- Windows: doppio clic su `build_windows.bat` → `dist\Crypto Radar Ultimate.exe`
- macOS: `./build_mac.sh` → `dist/Crypto Radar Ultimate.dmg`

## Struttura

| File | Ruolo |
|---|---|
| `app.py` | Pagina iniziale con il menu Crypto / Azioni |
| `trend_crypto.py` | Sezione Crypto |
| `stock_radar.py` | Sezione Azioni & Istituzioni |
| `stocks/` | Fonti dati, punteggio, avvisi, notifiche, pianificazione |
| `monitor.py` | Controllo orario da riga di comando |
| `indicators.py`, `charts.py` | Indicatori tecnici e grafici condivisi |
| `launcher.py` | Apre l'app in una finestra desktop (punto d'ingresso dell'eseguibile; `--monitor` per il solo controllo) |
| `crypto_radar.spec` | Configurazione PyInstaller per Windows e macOS |
| `build_windows.bat` / `build_mac.sh` | Script di build |
| `run_windows.bat` | Avvio rapido da sorgente |
| `tests/` | Test (`python -m pytest tests`) con dati simulati |
