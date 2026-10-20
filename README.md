# 📡 Crypto Radar Ultimate

App desktop (Streamlit + finestra nativa) con due sezioni:

- **🪙 Crypto** – scanner di trend per criptovalute
- **📈 Azioni & Istituzioni** – classifica delle azioni USA, giudizi delle grandi banche d'affari,
  Fed/BCE, SEC (8-K e 13F di BlackRock, Vanguard, State Street) e **avvisi automatici ogni ora**
- **⚙️ Impostazioni & Bot** – configurazione e **bot Telegram** con comandi (/compra, /titolo NVDA, /fed…)

## 🪙 Crypto

- **Prezzi BUY / SELL / STOP** con statistica storica di successo per ogni coin (vedi sotto)
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

### 🎯 Prezzi BUY / SELL / STOP e statistica storica

Per ogni titolo e ogni crypto l'app calcola un piano operativo basato sulla volatilità (ATR = movimento
medio di una candela/giornata):

| Livello | Calcolo | Significato |
|---|---|---|
| 🟢 **BUY** | prezzo attuale − 0,5 ATR | ordine d'acquisto (limite) su un piccolo ritracciamento, valido 5 candele |
| 🎯 **SELL** | BUY + 3 ATR | obiettivo dove vendere in guadagno |
| 🛑 **STOP** | BUY − 1,5 ATR | vendi se il prezzo scende qui (perdita limitata) |

Rendimento/rischio = 2:1. **Successo storico**: l'app riapplica lo stesso piano a tutti i segnali d'acquisto
passati di quel titolo (2 anni di dati per le azioni, ~1000 candele per le crypto) e misura in che percentuale
il prezzo ha toccato il SELL prima dello STOP, con il rendimento medio per operazione (massimo 20 giorni per
le azioni). Con il rapporto 2:1 basta superare circa il 33% per essere in guadagno nel tempo. Con meno di 5
operazioni storiche il dato viene segnalato come poco affidabile.

I livelli sono nella classifica, nell'analisi di ogni titolo (con le linee sul grafico), negli avvisi e nel
bot (`/compra`, `/titolo NVDA`).

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

**Canali**: **bot Telegram** (arriva sul telefono) e/o notifica di Windows.

### 🤖 Bot Telegram

Tutto si configura dalla pagina **⚙️ Impostazioni & Bot** (menu a sinistra), in 3 passi:

1. **Crea il bot**: su Telegram scrivi `/newbot` a **@BotFather**, scegli un nome e incolla il token nell'app.
2. **Avvia il bot**: premi *▶️ Avvia bot* (e spunta *Avvia automaticamente all'accesso a Windows*).
   Il bot resta acceso in background anche ad app chiusa e fa il **controllo ogni ora**.
3. **Collega il tuo account**: premi *Apri @tuobot su Telegram* e poi **Avvia**. Da quel momento il bot
   risponde solo a te.

Comandi (oppure i pulsanti che compaiono sotto la chat):

| Comando | Cosa fa |
|---|---|
| `/compra` | migliori titoli da comprare ora, con i motivi |
| `/evita` | titoli da evitare |
| `/classifica` | classifica di tutta la watchlist |
| `/titolo NVDA` (o solo `NVDA`) | analisi di un titolo: prezzo, punteggio, motivi, giudizi delle banche, notizie (anche fuori watchlist) |
| `/fed` | tono della Fed e ultimi comunicati/discorsi |
| `/banche` | ultimi giudizi di Goldman Sachs, J.P. Morgan, Morgan Stanley, Bank of America… |
| `/fondi` | cosa comprano/vendono BlackRock, Vanguard, State Street (13F) |
| `/notizie goldman` | ultime notizie (filtro facoltativo) |
| `/avvisi` | ultimi avvisi |
| `/controlla` | esegue subito il controllo completo |
| `/crypto` | migliori criptovalute ora (Binance, 1h) |
| `/watchlist`, `/aggiungi TSLA AMD`, `/rimuovi TSLA` | gestione della watchlist |
| `/stato` | stato del bot e delle fonti |

Nelle impostazioni inserisci anche **nome ed email** per la SEC (EDGAR li richiede a chi scarica i dati).

### Controllo ogni ora

- **Con il PC acceso** (consigliato): è il bot avviato al passo 2. Senza Telegram fa comunque il controllo
  orario con le notifiche di Windows. Da riga di comando: `python bot.py`.
- **Un controllo singolo**: `python monitor.py` (oppure `Crypto Radar Ultimate.exe --monitor`);
  `python monitor.py --test-notifica` per provare le notifiche.
- **Anche a PC spento** (GitHub Actions, workflow `monitor.yml`): nel repository su GitHub vai in
  *Settings → Secrets and variables → Actions* e crea i secret `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`,
  `SEC_USER_AGENT` e la variabile `MONITOR_ATTIVO` = `true` (facoltativa `WATCHLIST`, es. `AAPL,NVDA,JPM`).
  Con la variabile `BOT_SU_GITHUB` = `true` risponde anche ai comandi, ma solo una volta all'ora e
  **solo se il bot non è acceso anche sul PC**. Funziona dal branch principale (`main`). Ogni controllo dura
  1–2 minuti: su un repository privato 24 controlli al giorno usano gran parte dei 2000 minuti gratuiti
  mensili di GitHub.

Dati, impostazioni e report sono nella cartella `RadarUltimate` dentro la tua cartella utente
(`C:\Users\<nome>\RadarUltimate`): `config.json`, `report/ultimo_report.txt`, `report/storico_avvisi.csv`, `bot.log`.

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
| `app.py`, `nav.py` | Menu dell'app: Crypto / Azioni / Impostazioni |
| `settings_page.py` | Pagina Impostazioni & Bot |
| `bot.py` | Bot Telegram + controllo orario |
| `trend_crypto.py` | Sezione Crypto |
| `stock_radar.py` | Sezione Azioni & Istituzioni |
| `stocks/` | Fonti dati, punteggio, avvisi, notifiche, pianificazione |
| `monitor.py` | Controllo orario da riga di comando |
| `indicators.py`, `charts.py` | Indicatori tecnici e grafici condivisi |
| `launcher.py` | Apre l'app in una finestra desktop (punto d'ingresso dell'eseguibile; `--bot` e `--monitor` senza finestra) |
| `crypto_radar.spec` | Configurazione PyInstaller per Windows e macOS |
| `build_windows.bat` / `build_mac.sh` | Script di build |
| `run_windows.bat` | Avvio rapido da sorgente |
| `tests/` | Test (`python -m pytest tests`) con dati simulati |
