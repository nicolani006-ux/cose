"""Configurazione, watchlist e fonti monitorate."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("RADAR_HOME", Path.home() / "RadarUltimate"))
CONFIG_FILE = DATA_DIR / "config.json"
STATE_FILE = DATA_DIR / "stato_monitor.json"
REPORT_DIR = DATA_DIR / "report"
CACHE_DIR = DATA_DIR / "cache"

# ticker -> (nome, settore, nomi usati nelle notizie, nomi usati nei moduli 13F della SEC)
UNIVERSE: dict[str, tuple[str, str, list[str], list[str]]] = {
    "AAPL": ("Apple", "Tecnologia", ["Apple"], ["APPLE"]),
    "MSFT": ("Microsoft", "Tecnologia", ["Microsoft"], ["MICROSOFT"]),
    "NVDA": ("Nvidia", "Tecnologia", ["Nvidia", "NVIDIA"], ["NVIDIA"]),
    "AVGO": ("Broadcom", "Tecnologia", ["Broadcom"], ["BROADCOM"]),
    "AMD": ("AMD", "Tecnologia", ["AMD", "Advanced Micro Devices"], ["ADVANCED MICRO DEVICES"]),
    "ORCL": ("Oracle", "Tecnologia", ["Oracle"], ["ORACLE"]),
    "CRM": ("Salesforce", "Tecnologia", ["Salesforce"], ["SALESFORCE"]),
    "ADBE": ("Adobe", "Tecnologia", ["Adobe"], ["ADOBE"]),
    "INTC": ("Intel", "Tecnologia", ["Intel"], ["INTEL"]),
    "PLTR": ("Palantir", "Tecnologia", ["Palantir"], ["PALANTIR TECHNOLOGIES"]),
    "GOOGL": ("Alphabet", "Comunicazioni", ["Alphabet", "Google"], ["ALPHABET"]),
    "META": ("Meta Platforms", "Comunicazioni", ["Meta Platforms", "Meta"], ["META PLATFORMS"]),
    "NFLX": ("Netflix", "Comunicazioni", ["Netflix"], ["NETFLIX"]),
    "DIS": ("Walt Disney", "Comunicazioni", ["Disney"], ["DISNEY WALT", "WALT DISNEY"]),
    "T": ("AT&T", "Comunicazioni", ["AT&T"], ["AT T"]),
    "VZ": ("Verizon", "Comunicazioni", ["Verizon"], ["VERIZON COMMUNICATIONS"]),
    "AMZN": ("Amazon", "Consumi discrezionali", ["Amazon"], ["AMAZON COM"]),
    "TSLA": ("Tesla", "Consumi discrezionali", ["Tesla"], ["TESLA"]),
    "HD": ("Home Depot", "Consumi discrezionali", ["Home Depot"], ["HOME DEPOT"]),
    "MCD": ("McDonald's", "Consumi discrezionali", ["McDonald's", "McDonalds"], ["MCDONALDS"]),
    "NKE": ("Nike", "Consumi discrezionali", ["Nike"], ["NIKE"]),
    "WMT": ("Walmart", "Consumi di base", ["Walmart"], ["WALMART"]),
    "COST": ("Costco", "Consumi di base", ["Costco"], ["COSTCO WHSL", "COSTCO WHOLESALE"]),
    "KO": ("Coca-Cola", "Consumi di base", ["Coca-Cola", "Coca Cola"], ["COCA COLA"]),
    "PEP": ("PepsiCo", "Consumi di base", ["PepsiCo", "Pepsi"], ["PEPSICO"]),
    "PG": ("Procter & Gamble", "Consumi di base", ["Procter & Gamble", "P&G"], ["PROCTER GAMBLE"]),
    "JPM": ("JPMorgan Chase", "Finanza", ["JPMorgan", "JP Morgan", "J.P. Morgan"], ["JPMORGAN CHASE"]),
    "GS": ("Goldman Sachs", "Finanza", ["Goldman Sachs", "Goldman"], ["GOLDMAN SACHS GROUP"]),
    "MS": ("Morgan Stanley", "Finanza", ["Morgan Stanley"], ["MORGAN STANLEY"]),
    "BAC": ("Bank of America", "Finanza", ["Bank of America", "BofA"], ["BANK AMERICA", "BANK OF AMERICA"]),
    "WFC": ("Wells Fargo", "Finanza", ["Wells Fargo"], ["WELLS FARGO"]),
    "C": ("Citigroup", "Finanza", ["Citigroup", "Citi"], ["CITIGROUP"]),
    "BLK": ("BlackRock", "Finanza", ["BlackRock"], ["BLACKROCK"]),
    "STT": ("State Street", "Finanza", ["State Street"], ["STATE STREET"]),
    "SCHW": ("Charles Schwab", "Finanza", ["Charles Schwab", "Schwab"], ["SCHWAB CHARLES"]),
    "V": ("Visa", "Finanza", ["Visa"], ["VISA"]),
    "MA": ("Mastercard", "Finanza", ["Mastercard"], ["MASTERCARD"]),
    "BRK-B": ("Berkshire Hathaway", "Finanza", ["Berkshire Hathaway", "Berkshire"], ["BERKSHIRE HATHAWAY"]),
    "UNH": ("UnitedHealth", "Salute", ["UnitedHealth"], ["UNITEDHEALTH GROUP"]),
    "JNJ": ("Johnson & Johnson", "Salute", ["Johnson & Johnson", "J&J"], ["JOHNSON JOHNSON"]),
    "LLY": ("Eli Lilly", "Salute", ["Eli Lilly", "Lilly"], ["LILLY ELI", "ELI LILLY"]),
    "PFE": ("Pfizer", "Salute", ["Pfizer"], ["PFIZER"]),
    "MRK": ("Merck", "Salute", ["Merck"], ["MERCK"]),
    "ABBV": ("AbbVie", "Salute", ["AbbVie"], ["ABBVIE"]),
    "XOM": ("Exxon Mobil", "Energia", ["Exxon", "ExxonMobil"], ["EXXON MOBIL"]),
    "CVX": ("Chevron", "Energia", ["Chevron"], ["CHEVRON"]),
    "COP": ("ConocoPhillips", "Energia", ["ConocoPhillips"], ["CONOCOPHILLIPS"]),
    "BA": ("Boeing", "Industria", ["Boeing"], ["BOEING"]),
    "CAT": ("Caterpillar", "Industria", ["Caterpillar"], ["CATERPILLAR"]),
    "GE": ("GE Aerospace", "Industria", ["GE Aerospace", "General Electric"], ["GE AEROSPACE", "GENERAL ELECTRIC"]),
    "LMT": ("Lockheed Martin", "Industria", ["Lockheed Martin", "Lockheed"], ["LOCKHEED MARTIN"]),
    "NEE": ("NextEra Energy", "Utility", ["NextEra"], ["NEXTERA ENERGY"]),
    "DUK": ("Duke Energy", "Utility", ["Duke Energy"], ["DUKE ENERGY"]),
    "PLD": ("Prologis", "Immobiliare", ["Prologis"], ["PROLOGIS"]),
    "AMT": ("American Tower", "Immobiliare", ["American Tower"], ["AMERICAN TOWER"]),
}

# Banche d'affari di cui si seguono i giudizi degli analisti (regex sul nome usato da Yahoo Finance).
# Peso 1 = banche richieste esplicitamente, 0.5 = altre grandi banche.
ANALYST_FIRMS: dict[str, tuple[str, float]] = {
    "Goldman Sachs": (r"goldman", 1.0),
    "J.P. Morgan": (r"j\.?\s?p\.?\s?morgan|jpmorgan", 1.0),
    "Morgan Stanley": (r"morgan stanley", 1.0),
    "Bank of America": (r"\bb\s?of\s?a\b|bofa|bank of america|merrill", 1.0),
    "Citigroup": (r"\bciti", 0.5),
    "Wells Fargo": (r"wells fargo", 0.5),
    "UBS": (r"\bubs\b", 0.5),
    "Barclays": (r"barclays", 0.5),
    "Deutsche Bank": (r"deutsche", 0.5),
}

# Comunicati ufficiali (RSS).  "banca_centrale" = usati per il tono sui tassi.
OFFICIAL_FEEDS: dict[str, tuple[str, str]] = {
    "Fed – comunicati": ("https://www.federalreserve.gov/feeds/press_all.xml", "banca_centrale"),
    "Fed – discorsi": ("https://www.federalreserve.gov/feeds/speeches.xml", "banca_centrale"),
    "Fed – audizioni": ("https://www.federalreserve.gov/feeds/testimony.xml", "banca_centrale"),
    "BCE – comunicati": ("https://www.ecb.europa.eu/rss/press.html", "banca_centrale_ue"),
    "SEC – comunicati": ("https://www.sec.gov/news/pressreleases.rss", "regolatore"),
}

# Ricerche su Google News (aggrega titoli di Reuters, Bloomberg, WSJ/Dow Jones, CNBC, FT...).
NEWS_QUERIES: dict[str, str] = {
    "Goldman Sachs": '"Goldman Sachs" (strategist OR outlook OR forecast OR upgrade OR downgrade OR "price target" OR Solomon)',
    "J.P. Morgan": '(JPMorgan OR "JP Morgan") (strategist OR outlook OR forecast OR upgrade OR downgrade OR "price target" OR Dimon)',
    "Morgan Stanley": '"Morgan Stanley" (strategist OR outlook OR forecast OR upgrade OR downgrade OR "price target")',
    "Bank of America": '("Bank of America" OR BofA) (strategist OR outlook OR forecast OR upgrade OR downgrade OR "fund manager survey")',
    "Federal Reserve": '("Federal Reserve" OR "Fed chair" OR FOMC) (rates OR inflation OR policy)',
    "BlackRock": 'BlackRock (Fink OR outlook OR "investment institute" OR stake OR buys OR sells)',
    "Vanguard": 'Vanguard (outlook OR forecast OR stake OR fund)',
    "State Street": '"State Street" (outlook OR "global advisors" OR stake OR SPDR)',
    "Casa Bianca / Tesoro USA": '("White House" OR "Treasury Secretary" OR President) (tariffs OR economy OR trade OR stocks)',
    "Unione Europea / BCE": '("European Central Bank" OR Lagarde OR "European Commission") (rates OR tariffs OR economy)',
    "Wall Street": 'stocks ("price target" OR upgrade OR downgrade) Wall Street',
}

# Gestori di cui si confrontano gli ultimi due moduli 13F (portafogli azionari USA, trimestrali).
# Più CIK possibili: si usa quello con il 13F più recente (BlackRock ha cambiato
# società capogruppo nel 2024).
FILERS_13F: dict[str, list[int]] = {
    "BlackRock": [2012383, 1364742],
    "Vanguard": [102909],
    "State Street": [93751],
}

DEFAULT_CONFIG: dict = {
    "watchlist": list(UNIVERSE),
    "telegram_token": "",
    "telegram_chat_id": "",
    "notifiche_desktop": True,
    # La SEC richiede un User-Agent con nome ed email: https://www.sec.gov/os/accessing-edgar-data
    "sec_user_agent": "",
    "livello_minimo_avviso": "media",  # "info", "media" o "alta"
    "invia_riepilogo_sempre": False,
    "controlla_13f": True,
    "controlla_8k": True,
    "controlla_congresso": True,
    # Quantità da comprare (0 = non calcolarla). Stessa valuta dei prezzi: $ per le azioni, USDT per le crypto
    "capitale": 0,
    "rischio_max_operazione": 50,   # perdita massima accettata allo STOP per ogni operazione
    "posizione_max_pct": 75,        # massimo del capitale in un solo titolo
    "riserva_cassa_pct": 10,        # liquidità sempre lasciata da parte
    "ordine_max": 0,                # spesa massima per ordine (0 = nessun limite)  # operazioni dichiarate dai deputati USA (House Clerk, STOCK Act)
    # Test delle previsioni crypto sui prezzi reali (file report/test_previsioni.xlsx)
    "test_previsioni": True,
    "test_exchange": "binance",
    "test_quote": "USDT",
    "test_timeframe": "1h",
    "test_coins": 30,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    # Variabili d'ambiente (usate dal controllo su GitHub Actions) hanno la precedenza.
    for key, env in (("telegram_token", "RADAR_TELEGRAM_TOKEN"), ("telegram_chat_id", "RADAR_TELEGRAM_CHAT_ID"),
                     ("sec_user_agent", "RADAR_SEC_USER_AGENT")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if os.environ.get("RADAR_NOTIFICHE_DESKTOP") == "0":
        cfg["notifiche_desktop"] = False
    if os.environ.get("RADAR_WATCHLIST"):
        cfg["watchlist"] = os.environ["RADAR_WATCHLIST"].replace(" ", "").split(",")
    cfg["watchlist"] = [t.strip().upper() for t in cfg.get("watchlist", []) if t.strip()]
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def ticker_info(ticker: str) -> tuple[str, str, list[str], list[str]]:
    return UNIVERSE.get(ticker, (ticker, "Altro", [], []))
