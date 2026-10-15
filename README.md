# 📡 Crypto Radar Ultimate

Scanner di trend per criptovalute con interfaccia desktop (Streamlit + finestra nativa).

## Cosa fa

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
streamlit run trend_crypto.py
```

## Creare l'eseguibile in locale

- Windows: doppio clic su `build_windows.bat` → `dist\Crypto Radar Ultimate.exe`
- macOS: `./build_mac.sh` → `dist/Crypto Radar Ultimate.dmg`

## Struttura

| File | Ruolo |
|---|---|
| `trend_crypto.py` | L'app Streamlit (indicatori, radar, grafici, notizie, export) |
| `launcher.py` | Apre l'app in una finestra desktop (punto d'ingresso dell'eseguibile) |
| `crypto_radar.spec` | Configurazione PyInstaller per Windows e macOS |
| `build_windows.bat` / `build_mac.sh` | Script di build |
| `run_windows.bat` | Avvio rapido da sorgente |
| `tests/` | Test (`python -m pytest tests`) con un exchange simulato |
