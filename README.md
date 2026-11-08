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

## 🧪 Test delle previsioni (prezzi reali)

Per verificare se le previsioni funzionano davvero, ad ogni controllo orario il bot:

1. registra ogni crypto con segnale d'acquisto (Binance, USDT, prime 30 per volume, timeframe 1h):
   probabilità ipotizzata, prezzo BUY e SELL, margine ipotizzato;
2. la segue sulle candele reali successive con le stesse regole della statistica storica
   (ordine BUY valido 5 candele → chiusura al SELL, allo STOP o dopo 48 ore);
3. aggiorna il file **`RadarUltimate\report\test_previsioni.xlsx`**.

| Foglio | Contenuto |
|---|---|
| **Previsioni** | A crypto · B probabilità di rialzo ipotizzata · C prezzo BUY → SELL ipotizzato · D margine ipotizzato · E guadagno effettivo; poi esito, date, STOP, prezzo d'uscita, durata, guadagno netto commissioni (0,1% + 0,1%), massimo guadagno e massima perdita toccati, variazione del prezzo dopo 48 ore |
| **Statistiche** | ordini eseguiti, successo reale contro probabilità ipotizzata, Brier score, margine ipotizzato contro guadagno reale, risultato con 100 € per operazione, profit factor, drawdown, miglior/peggior operazione, durata media… (ognuna con la spiegazione) |
| **Per crypto** | risultati di ogni coin |
| **Calibrazione** | per fasce di probabilità: quante volte la previsione si è avverata davvero |
| **Andamento** | risultato cumulato operazione dopo operazione, con grafico |

La **probabilità ipotizzata** è la percentuale storica con cui il piano di quella coin ha raggiunto il SELL
prima dello STOP, avvicinata alla media generale quando i casi storici sono pochi (così 2 su 2 non diventa 100%).

Si vede nella pagina **🧪 Test previsioni** dell'app (con il pulsante per scaricare l'Excel); dal telefono
il comando `/test` del bot manda il riepilogo e il file. Quando un'operazione del test si chiude arriva un avviso.
Si può disattivare o cambiare exchange/timeframe in `config.json` (`test_previsioni`, `test_exchange`,
`test_timeframe`, `test_coins`).

**Solo l'Excel, senza app** (servono solo `ccxt`, `pandas`, `numpy`, `openpyxl`):

- doppio clic su `excel_previsioni.bat`, oppure `python excel_previsioni.py` (`--loop 60` per ripeterlo ogni ora,
  `--solo-excel` per rigenerare il file senza internet, `--cartella DIR` per scegliere dove salvare i dati);
- con l'eseguibile: `"Crypto Radar Ultimate.exe" --excel`;
- in Claude Code: la skill **`/excel-previsioni`** (in `.claude/skills/excel-previsioni`) fa solo questo
  aggiornamento e consegna il file. In una sessione cloud va consentito il dominio `data-api.binance.vision`
  (dati pubblici ufficiali di Binance, non bloccati negli USA come `api.binance.com`) nelle impostazioni di
  rete dell'ambiente; lo storico viene salvato nella cartella `dati_previsioni` del repository.

**Come vedere se il test sta girando**: pagina **🧪 Test previsioni** → "Ultimo aggiornamento del test" e
"Registro degli aggiornamenti" (file `RadarUltimate\report\registro_test.txt`, una riga per ogni
aggiornamento). `excel_previsioni.bat` apre una finestra nera che mostra ogni aggiornamento e il percorso del
file; serve solo se hai scaricato il progetto e installato Python, con l'app basta il bot.

## 📈 Azioni & Istituzioni

Per ogni titolo della watchlist (55 grandi aziende USA, modificabile) calcola un **punteggio da -100 a +100**
e indica i **migliori candidati all'acquisto** e quelli da evitare, spiegando sempre il perché:

| Peso | Componente | Fonte |
|---|---|---|
| 35% | Analisi tecnica (EMA 20/50/200, MACD, RSI, volumi, breakout) | Yahoo Finance |
| 30% | Upgrade/downgrade e target price di **Goldman Sachs, J.P. Morgan, Morgan Stanley, Bank of America** (peso pieno) e Citi, Wells Fargo, UBS, Barclays, Deutsche Bank (peso ridotto) | Yahoo Finance |
| 10% | Sentiment delle notizie che citano il titolo; comunicazioni SEC 8-K negative | Google News, SEC EDGAR |
| 10% | Azioni comprate/vendute da **BlackRock, Vanguard, State Street** (confronto ultimi due 13F) | SEC EDGAR |
| 10% | Tono della **Federal Reserve** (tassi giù/su) e suo effetto sul settore | federalreserve.gov |
| 5% | Acquisti e vendite dichiarati dai **deputati USA** (Pelosi & co., STOCK Act) | House Clerk |

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
2. **Avvia il bot**: premi *▶️ Avvia bot* e attendi (la prima volta può volerci fino a un minuto: compare
   "🟢 Bot in esecuzione"); spunta *Avvia automaticamente all'accesso a Windows*. Il bot resta acceso in
   background anche ad app chiusa e fa il **controllo ogni ora**. La pagina mostra lo stato del collegamento
   con Telegram, l'ultimo errore e il registro `bot.log`; nel Task Manager il bot compare come un secondo
   processo "Crypto Radar Ultimate".
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
| `/congresso pelosi` (o `/congresso NVDA`) | operazioni dichiarate dai deputati USA |
| `/notizie goldman` | ultime notizie (filtro facoltativo) |
| `/avvisi` | ultimi avvisi |
| `/controlla` | esegue subito il controllo completo |
| `/crypto` | migliori criptovalute ora (Binance, 1h) |
| `/test` | risultati del test delle previsioni + file Excel |
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

## 🧰 Skill di day-trading (da day-trader-skill)

In `.claude/skills/` ci sono le 11 skill di [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
di Jake Schincariol (licenza MIT, `.claude/skills/LICENSE-day-trader-skill`). In Claude Code, aprendo questa
cartella, si usano con lo slash:

| Comando | Cosa fa |
|---|---|
| `/trader-scanner` | Cerca setup su migliaia di azioni USA: rotture del range di apertura, bandiere, ipervenduto |
| `/trader-news` | Notizie Yahoo e comunicazioni SEC 8-K su ogni titolo seguito, solo le novità |
| `/trader-entry` | Segue un setup candela per candela e dice quando entrare |
| `/trader-exit` | Fissa STOP e obiettivo prima di entrare |
| `/trader-timemachine` | Rigioca 10.000 volte il resto della seduta prima di rischiare un dollaro |
| `/trader-risk` | Controlla ogni ordine contro i tuoi limiti (PASS / BLOCK) |
| `/trader-budget` | Quante azioni comprare con il tuo budget e la perdita massima per operazione |
| `/trader-broker` | Manda gli ordini approvati al broker (Alpaca, **simulazione di default**, mai senza il tuo sì) |
| `/trader-alerts` | Avvisi su prezzo, STOP, obiettivo e campanella di apertura |
| `/trader-analyst` | Legge i bilanci dalla SEC e riassume cosa è cambiato |
| `/trader-pelosi` | Operazioni in borsa dichiarate dai deputati USA, appena depositate |

Sono pensate per il **day trading su azioni USA** (orari della borsa di New York, candele da 5 minuti): i dati
e i file di lavoro finiscono nella cartella `trader/` del progetto (esclusa da git). Nessuna skill piazza ordini
da sola: `/trader-broker` funziona solo se colleghi tu un broker (server MCP di Alpaca o connettore IBKR) e va
sempre lasciato su **"Needs approval"**, mai "Always allow".

Prima di installarle tutto il codice è stato letto riga per riga: nessun comportamento malevolo, contattano
solo fonti pubbliche (Yahoo Finance, SEC, House Clerk, Nasdaq Trader, Stooq). Due modifiche di sicurezza:
`trader-pelosi/ptr.py` accetta solo DocID numerici (prima un valore manipolato poteva scrivere fuori dalla
cartella dei PDF) e `trader-broker/SKILL.md` passa la tua risposta al comando tra apici singoli (prima, tra
virgolette, la shell avrebbe potuto eseguirne pezzi). I loro 304 test girano con i nostri (`tests/day_trader`).

### Cosa è entrato nell'app

- **Time Machine** (da `trader-timemachine`): per ogni piano BUY/SELL/STOP di crypto e azioni, 2.000
  simulazioni del futuro costruite con le candele passate della stessa ora del giorno e un verdetto
  rispetto al pareggio. La **probabilità SELL** mostrata ovunque è la media tra storia reale e Time Machine;
  nel test delle previsioni sono registrate anche separate, con il Brier score di ciascuna.
- **Quantità da comprare** (da `trader-budget`): in *Impostazioni → Capitale e rischio* imposti capitale e
  perdita massima per operazione; ogni piano dice quante azioni/unità comprare e quale limite ha deciso.
- **Congresso USA** (da `trader-pelosi`): nuova scheda *🗳️ Congresso USA*, comando `/congresso` del bot, avvisi
  quando un deputato compra o vende un titolo della watchlist (o oltre 250.000 $) e 5% del punteggio. Su
  Windows i PDF si leggono con `pypdf` (incluso), senza installare nulla.
- **Bilanci SEC** (da `trader-analyst`): nell'analisi del titolo e in `/titolo` del bot, ricavi, utile per
  azione e margini trimestre per trimestre con la crescita rispetto all'anno prima.

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
| `previsioni.py`, `test_page.py` | Test delle previsioni sui prezzi reali e relativa pagina |
| `excel_previsioni.py`, `.claude/skills/excel-previsioni` | Solo test + Excel, da riga di comando o come skill di Claude Code |
| `crypto_core.py` | Dati crypto senza interfaccia (exchange, scansione) |
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

## Novità della versione 2.0.0

- **Crypto più veloce e coerente**: si usano solo candele chiuse; candele scaricate in modo incrementale;
  statistica e Time Machine ricalcolate solo a candela nuova; indicatori calcolati una volta per coin;
  limite di richieste dell'exchange rispettato con nuovi tentativi (scansione ripetuta: da ~7 s a ~3 s).
- **Avvisi meno rumorosi**: stessa notizia da più ricerche = un avviso; notizie generiche solo "info";
  ore di silenzio (es. `23-7`) e numero massimo di avvisi per messaggio nelle Impostazioni.
- **Bot**: token salvato cifrato (protezione di Windows), collegamento solo con codice
  (`t.me/<bot>?start=CODICE` dall'app), comandi in parallelo, riavvio automatico dopo un errore,
  avviso quando esce una nuova versione.
- **Test delle previsioni**: verdetto statistico ("il modello ha un vantaggio?") dopo 100 operazioni chiuse;
  può girare anche su GitHub Actions a PC spento (variabile `TEST_SU_GITHUB = true`, Excel negli Artifacts).
- **Fonti azioni**: nuovi tentativi sugli errori HTTP e riserva Yahoo chart quando yfinance non funziona.
- **Build**: versioni bloccate in `constraints.txt` (Dependabot propone gli aggiornamenti), autotest
  dell'eseguibile in CI (`--self-test`), release automatica con un tag `v*`.
