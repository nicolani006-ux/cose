"""Investimento automatico dei segnali BUY massimo.

Ogni segnale BUY massimo confermato dal motore diventa un acquisto, senza chiedere conferma, entro
i limiti scelti nella pagina "Investimento automatico":

* importo di ogni operazione = ``auto_percentuale`` % della liquidità disponibile (meno ``auto_riserva``),
  al massimo ``auto_importo_max`` e mai sotto ``auto_importo_min``;
* al massimo ``auto_max_trade_giorno`` acquisti al giorno e ``auto_max_posizioni`` operazioni aperte;
* se le operazioni chiuse oggi perdono più di ``auto_stop_perdita_giorno``, per oggi non si compra più.

Ogni operazione segue lo stesso piano del segnale (e del test delle previsioni):

    ordine BUY limite al prezzo BUY, valido 5 candele ─► comprato ─► ordine SELL limite sul prezzo SELL
    (resta sull'exchange anche a PC spento) e controllo continuo dello STOP: se il prezzo scende allo STOP
    l'ordine SELL si annulla e si vende subito. Dopo l'orizzonte del piano (48 candele da 1h) si chiude.

Due modalità:

* **simulazione** (predefinita): soldi virtuali. Il capitale sono i versamenti registrati nella pagina
  (anche automatici, con il piano di versamento). Gli ordini si eseguono sul prezzo reale di mercato.
* **reale**: ordini veri sull'exchange con le chiavi API dell'utente (cifrate con DPAPI, mai mostrate
  dall'interfaccia). Il capitale è la liquidità libera (USDT) sul conto: i soldi che versi sull'exchange
  vengono investiti dai segnali successivi. Le chiavi con il prelievo abilitato vengono rifiutate.

Tutto gira nel thread del motore dei segnali (un solo processo alla volta): niente doppi acquisti.
I comandi dell'interfaccia (es. "chiudi tutto") passano dal database e li esegue il motore.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import date, datetime

from indicators import HORIZON_BARS, PLAN_FILL_BARS
from stocks import secret
from stocks.config import DATA_DIR
from webapp import store

log = logging.getLogger("radar.web.auto")

MODES = ("simulazione", "reale")
OPEN = ("ordine", "aperta")
FEE_PCT = 0.1                      # commissione per lato se l'exchange non è in FEES_PCT
# Commissione taker spot di base per lato (%), livello più basso di volume. Gli exchange "eu"/"my" sono le
# entità con licenza MiCA per i clienti UE (Binance non serve più l'Italia da luglio 2026).
FEES_PCT = {"binance": 0.1, "bybit": 0.1, "bybiteu": 0.1, "okx": 0.1, "myokx": 0.1, "kucoin": 0.1,
            "gateio": 0.1, "mexc": 0.05, "kraken": 0.4, "bitvavo": 0.25, "cryptocom": 0.5, "coinbase": 0.6}
KEYS_FILE = DATA_DIR / "chiavi_exchange.json"
EXCHANGE_MIN_COST = 5.0            # minimo per ordine se l'exchange non lo dichiara (Binance: 5 USDT)
RELEASE_TRIES = 5                  # dopo aver annullato il SELL, quante volte (1 s) aspettare che le coin si liberino
LOST_ORDER_GRACE_MS = 10 * 60_000  # ordine BUY senza risposta dall'exchange: dopo la scadenza + 10 min si chiude
DEAD_ORDER = ("canceled", "cancelled", "rejected", "expired")


def plan_horizon(cfg: dict, tf: str) -> int:
    """Durata massima dell'operazione in candele: con la strategia dip è regolabile (SELL lontani = più tempo)."""
    if cfg.get("strategia") == "dip" and cfg.get("dip_candele_max"):
        return max(int(cfg["dip_candele_max"]), 1)
    if cfg.get("strategia") == "rimbalzo" and cfg.get("rimb_candele_max"):
        return max(int(cfg["rimb_candele_max"]), 1)
    return HORIZON_BARS.get(tf, 48)


def fee_pct(exchange: str | None) -> float:
    return FEES_PCT.get(exchange or "", FEE_PCT)


def tf_ms(tf: str) -> int:
    from webapp.engine import timeframe_ms

    return timeframe_ms(tf)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Chiavi API (solo modalità reale)
# --------------------------------------------------------------------------- #

def load_keys() -> dict:
    try:
        raw = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: secret.unprotect(v) if k in ("key", "secret", "password") else v for k, v in raw.items()}


def save_keys(exchange: str, key: str, sec: str, password: str = "", info: dict | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {"exchange": exchange, "key": secret.protect(key), "secret": secret.protect(sec),
            "password": secret.protect(password), "saved": datetime.now().isoformat(timespec="seconds"),
            **(info or {})}
    tmp = KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(KEYS_FILE)


def delete_keys() -> None:
    try:
        KEYS_FILE.unlink()
    except OSError:
        pass


def keys_public() -> dict:
    """Stato delle chiavi per l'interfaccia (senza mai restituire le chiavi)."""
    k = load_keys()
    if not k.get("key"):
        return {"configured": False}
    return {"configured": True, "exchange": k.get("exchange"), "key_hint": "…" + k["key"][-4:],
            "saved": k.get("saved"), "verified": k.get("verified"), "withdraw": k.get("withdraw")}


def private_exchange(keys: dict | None = None):
    import ccxt

    keys = keys or load_keys()
    if not keys.get("key"):
        raise RuntimeError("chiavi API non configurate")
    params = {"apiKey": keys["key"], "secret": keys["secret"], "enableRateLimit": True, "timeout": 20000,
              # adjustForTimeDifference: l'orologio di Windows può sfasarsi di qualche secondo e Bybit/Binance
              # rifiutano le richieste fuori dalla recvWindow
              "options": {"defaultType": "spot", "adjustForTimeDifference": True, "recvWindow": 10000}}
    if keys.get("password"):
        params["password"] = keys["password"]
    ex = getattr(ccxt, keys["exchange"])(params)
    session = getattr(ex, "session", None)
    if session is not None:
        session.trust_env = True
    ex.load_markets()
    return ex


def verify_keys(exchange: str, key: str, sec: str, password: str = "", quote: str = "USDT") -> dict:
    """Prova le chiavi: lettura del saldo e, su Binance, i permessi. Solleva ValueError con un messaggio
    comprensibile se non vanno bene (prelievo abilitato, trading spot non abilitato, chiavi errate)."""
    import ccxt

    try:
        ex = private_exchange({"exchange": exchange, "key": key, "secret": sec, "password": password})
        bal = ex.fetch_balance()
    except ccxt.AuthenticationError as e:
        raise ValueError(f"chiavi rifiutate dall'exchange: {e}") from e
    except ccxt.BaseError as e:
        raise ValueError(f"exchange non raggiungibile: {type(e).__name__}: {e}") from e
    info = {"verified": datetime.now().isoformat(timespec="seconds"), "withdraw": None,
            "free": float((bal.get("free") or {}).get(quote) or 0)}
    if exchange == "binance":
        try:
            r = ex.sapiGetAccountApiRestrictions()
        except ccxt.BaseError:
            r = None
        if r:
            if str(r.get("enableWithdrawals")).lower() == "true":
                raise ValueError("questa chiave può PRELEVARE fondi: creane una con i prelievi disattivati "
                                 "(serve solo 'Abilita trading spot e margin')")
            if str(r.get("enableSpotAndMarginTrading")).lower() != "true":
                raise ValueError("la chiave non ha il permesso di trading spot: abilita 'Spot e Margin Trading'")
            info["withdraw"] = False
    elif exchange in ("bybit", "bybiteu"):
        try:
            r = (ex.privateGetV5UserQueryApi() or {}).get("result") or {}
        except ccxt.BaseError:
            r = {}
        if r:
            perms = r.get("permissions") or {}
            if "Withdraw" in (perms.get("Wallet") or []):
                raise ValueError("questa chiave può PRELEVARE fondi: su Bybit creane una senza il permesso "
                                 "'Wallet → Withdraw' (serve solo 'Spot → Trade')")
            if str(r.get("readOnly")) == "1" or "SpotTrade" not in (perms.get("Spot") or []):
                raise ValueError("la chiave non può fare trading spot: scegli 'Read-Write' e abilita 'Spot → Trade'")
            info["withdraw"] = False
    return info


# --------------------------------------------------------------------------- #
# Broker: simulazione e reale hanno le stesse operazioni
# --------------------------------------------------------------------------- #

def _public_last(exchange: str, symbol: str) -> float:
    import crypto_core

    t = crypto_core._retry(crypto_core.get_exchange(exchange).fetch_ticker, symbol)
    return float(t.get("last") or t.get("close"))


def spread_of(book: dict) -> float | None:
    """Differenza % fra il miglior prezzo di vendita (ask) e di acquisto (bid) del libro ordini."""
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    return (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask >= bid else None


def _public_spread(exchange: str, symbol: str) -> float | None:
    import crypto_core

    return spread_of(crypto_core._retry(crypto_core.get_exchange(exchange).fetch_order_book, symbol, limit=5))


class PaperBroker:
    """Soldi virtuali: ordini eseguiti sul prezzo reale di mercato, commissioni comprese."""

    mode = "simulazione"

    def __init__(self, exchange: str, last_price=None, spread=None):
        self.exchange = exchange
        self.fee = fee_pct(exchange)
        self._last = last_price or (lambda sym: _public_last(exchange, sym))
        # in simulazione si controlla lo stesso libro ordini pubblico (con prezzi finti dei test: niente controllo)
        self._spread = spread or ((lambda sym: None) if last_price else (lambda sym: _public_spread(exchange, sym)))

    def spread_pct(self, symbol: str) -> float | None:
        return self._spread(symbol)

    def last(self, symbol: str) -> float:
        return self._last(symbol)

    def free_quote(self, quote: str) -> float:
        return wallet("simulazione")["cash"]

    def min_cost(self, symbol: str) -> float:
        return EXCHANGE_MIN_COST

    def buy_limit(self, symbol: str, cost: float, price: float, client_id: str | None = None) -> dict:
        return {"id": f"sim-{int(time.time() * 1000)}", "qty": cost / price, "price": price}

    def find_order(self, client_id: str, symbol: str) -> dict | None:
        return None

    def replace_sell(self, p: dict) -> tuple[str, float] | None:
        return self.sell_limit(p["symbol"], p["qty"], p["sell"]), p["qty"]

    def buy_status(self, p: dict) -> dict:
        """filled: quantità comprata (al netto della commissione); done: ordine concluso."""
        last = self.last(p["symbol"])
        if last <= p["buy"]:
            entry = min(last, p["buy"])
            return {"done": True, "filled": p["cost"] / entry * (1 - self.fee / 100), "avg": entry,
                    "cost": p["cost"]}
        return {"done": False, "filled": 0.0, "last": last}

    def cancel(self, symbol: str, order_id: str | None) -> None:
        pass

    def sell_limit(self, symbol: str, qty: float, price: float) -> str:
        return f"sim-tp-{int(time.time() * 1000)}"

    def sell_status(self, p: dict, last: float) -> dict:
        if last >= p["sell"]:
            return {"done": True, "avg": p["sell"], "proceeds": p["qty"] * p["sell"] * (1 - self.fee / 100)}
        return {"done": False, "status": "open"}

    def sell_market(self, p: dict, last: float) -> dict:
        return {"avg": last, "proceeds": p["qty"] * last * (1 - self.fee / 100)}


class LiveBroker:
    """Ordini veri sull'exchange (ccxt)."""

    mode = "reale"

    def __init__(self, ex=None, sleep=time.sleep):
        self.ex = ex or private_exchange()
        self.fee = fee_pct(getattr(self.ex, "id", None))
        self._sleep = sleep

    def last(self, symbol: str) -> float:
        t = self.ex.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close"))

    def free_quote(self, quote: str) -> float:
        return float((self.ex.fetch_balance().get("free") or {}).get(quote) or 0)

    def spread_pct(self, symbol: str) -> float | None:
        return spread_of(self.ex.fetch_order_book(symbol, limit=5))

    def min_cost(self, symbol: str) -> float:
        lim = (self.ex.market(symbol).get("limits") or {}).get("cost") or {}
        return max(float(lim.get("min") or 0), EXCHANGE_MIN_COST)

    def buy_limit(self, symbol: str, cost: float, price: float, client_id: str | None = None) -> dict:
        qty = float(self.ex.amount_to_precision(symbol, cost / price))
        px = float(self.ex.price_to_precision(symbol, price))
        # il nostro id sull'ordine: se la risposta dell'exchange si perde, l'ordine si ritrova (find_order)
        o = self.ex.create_limit_buy_order(symbol, qty, px, {"clientOrderId": client_id} if client_id else {})
        return {"id": o["id"], "qty": qty, "price": px}

    def find_order(self, client_id: str, symbol: str) -> dict | None:
        """Cerca un ordine per il nostro id fra quelli aperti, chiusi e annullati."""
        import ccxt

        for name in ("fetch_open_orders", "fetch_closed_orders", "fetch_canceled_orders"):
            fn = getattr(self.ex, name, None)
            if fn is None:
                continue
            try:
                for o in fn(symbol) or []:
                    if o.get("clientOrderId") == client_id:
                        return o
            except (ccxt.NotSupported, ccxt.ArgumentsRequired):
                continue
        return None

    def _order(self, order_id: str, symbol: str) -> dict:
        """Stato di un ordine. Su Bybit (conto Unified) fetch_order funziona solo con acknowledged=True
        (ordini fra gli ultimi 500): se non lo trova si cerca fra gli ordini chiusi."""
        import ccxt

        try:
            if str(getattr(self.ex, "id", "")).startswith("bybit"):
                return self.ex.fetch_order(order_id, symbol, {"acknowledged": True})
            return self.ex.fetch_order(order_id, symbol)
        except ccxt.OrderNotFound:
            if (getattr(self.ex, "has", None) or {}).get("fetchClosedOrder"):
                return self.ex.fetch_closed_order(order_id, symbol)
            raise

    def buy_status(self, p: dict) -> dict:
        o = self._order(p["buy_order"], p["symbol"])
        filled = float(o.get("filled") or 0)
        done = o.get("status") in ("closed", "canceled", "expired", "rejected")
        out = {"done": done, "filled": filled, "avg": float(o.get("average") or o.get("price") or p["buy"]),
               "cost": float(o.get("cost") or filled * float(o.get("average") or p["buy"]))}
        if done and filled > 0:
            out["filled"] = self._sellable(p["symbol"], filled)
        return out

    def _sellable(self, symbol: str, filled: float, guess_if_empty: bool = True) -> float:
        """Quantità vendibile: la commissione può essere trattenuta sulla coin comprata. Subito dopo l'acquisto
        il saldo può non essere ancora aggiornato (guess_if_empty); in vendita invece 0 vuol dire 0."""
        import ccxt

        base = self.ex.market(symbol)["base"]
        free = float((self.ex.fetch_balance().get("free") or {}).get(base) or 0)
        if free > 0:
            qty = min(filled, free)
        elif guess_if_empty:
            qty = filled * (1 - self.fee / 100)
        else:
            return 0.0
        try:
            return float(self.ex.amount_to_precision(symbol, qty))
        except ccxt.InvalidOrder:  # sotto la quantità minima dell'exchange: residuo non vendibile
            return 0.0

    def cancel(self, symbol: str, order_id: str | None) -> None:
        import ccxt

        if not order_id:
            return
        try:
            self.ex.cancel_order(order_id, symbol)
        except ccxt.OrderNotFound:  # già eseguito o annullato
            pass

    def sell_limit(self, symbol: str, qty: float, price: float) -> str:
        px = float(self.ex.price_to_precision(symbol, price))
        return self.ex.create_limit_sell_order(symbol, qty, px)["id"]

    def sell_status(self, p: dict, last: float) -> dict:
        if not p.get("sell_order"):
            return {"done": False}
        o = self._order(p["sell_order"], p["symbol"])
        if o.get("status") == "closed":
            avg = float(o.get("average") or o.get("price") or p["sell"])
            # se l'exchange non riporta il controvalore lo si ricava (con 0 l'operazione risulterebbe persa al 100%)
            gross = float(o.get("cost") or 0) or float(o.get("filled") or p["qty"]) * avg
            return {"done": True, "avg": avg, "proceeds": gross * (1 - self.fee / 100)}
        return {"done": False, "status": o.get("status"), "filled": float(o.get("filled") or 0)}

    def replace_sell(self, p: dict) -> tuple[str, float] | None:
        """Rimette il SELL limite (mai inviato, o annullato sull'exchange) con le coin davvero disponibili."""
        qty = self._sellable(p["symbol"], p["qty"], guess_if_empty=False)
        if qty <= 0:
            return None
        return self.sell_limit(p["symbol"], qty, p["sell"]), qty

    def sell_market(self, p: dict, last: float) -> dict:
        """Vende subito ciò che resta. Tiene conto della parte già venduta dall'ordine SELL limite
        (anche se si è eseguito proprio mentre scattava lo STOP) e non riprova a vendere coin che non ci sono più."""
        import ccxt

        sym = p["symbol"]
        sold_qty = sold_cost = 0.0
        if p.get("sell_order"):
            self.cancel(sym, p["sell_order"])
            try:
                o = self._order(p["sell_order"], sym)
                sold_qty, sold_cost = float(o.get("filled") or 0), float(o.get("cost") or 0)
            except ccxt.BaseError:
                pass
        left = max(p["qty"] - sold_qty, 0.0)
        mn = float(((self.ex.market(sym).get("limits") or {}).get("amount") or {}).get("min") or 0)
        # dopo l'annullamento del SELL l'exchange può metterci un attimo a liberare le coin: si aspetta
        qty = 0.0
        for i in range(RELEASE_TRIES):
            qty = self._sellable(sym, left, guess_if_empty=False)
            if left < mn or qty >= left * 0.98:
                break
            self._sleep(1)
        if qty <= 0 and left >= mn and left * last >= self.min_cost(sym):
            # niente da vendere ma le coin dovrebbero esserci: meglio riprovare al prossimo controllo
            # che segnare l'operazione chiusa lasciando le coin senza SELL e senza STOP
            raise RuntimeError(f"{sym}: coin non ancora disponibili per la vendita, riprovo")
        cost = 0.0
        if qty > 0 and qty >= mn:
            o = self.ex.create_market_sell_order(sym, qty)
            avg_px = float(o.get("average") or 0)
            cost = float(o.get("cost") or 0)
            if not cost and o.get("id"):  # Bybit risponde solo con l'id: si legge l'esecuzione vera
                try:
                    f = self._order(o["id"], sym)
                    cost, avg_px = float(f.get("cost") or 0), float(f.get("average") or 0)
                except ccxt.BaseError:
                    pass
            cost = cost or qty * (avg_px or last)
        else:
            qty = 0.0
        total_qty = sold_qty + qty
        avg = (sold_cost + cost) / total_qty if total_qty else last
        return {"avg": avg, "proceeds": (sold_cost + cost) * (1 - self.fee / 100)}


def _network_error(e: Exception) -> bool:
    try:
        import ccxt
    except ImportError:  # pragma: no cover
        return False
    return isinstance(e, (ccxt.NetworkError, TimeoutError, ConnectionError))


def make_broker(cfg: dict):
    if cfg.get("auto_modalita") == "reale":
        return LiveBroker()
    return PaperBroker(cfg["segnali_exchange"])


# --------------------------------------------------------------------------- #
# Capitale
# --------------------------------------------------------------------------- #

def wallet(mode: str, last_prices: dict | None = None) -> dict:
    """Capitale della modalità: versato, liquidità, investito, risultato. In modalità reale la liquidità
    vera è sull'exchange (vedi `status`), qui c'è quanto risulta dalle operazioni registrate."""
    deps = store.deposits(mode)
    pos = store.positions(mode)
    deposited = sum(d["amount"] for d in deps)
    realized = sum(p["pnl"] or 0 for p in pos if p["status"] == "chiusa")
    tied = sum(p.get("cost") or 0 for p in pos if p["status"] in OPEN)
    unreal = 0.0
    for p in pos:
        if p["status"] == "aperta":
            last = (last_prices or {}).get(p["symbol"]) or p.get("last")
            if last:
                unreal += p["qty"] * last * (1 - fee_pct(p.get("exchange")) / 100) - p["cost"]
    cash = deposited + realized - tied
    return {"deposited": deposited, "realized": realized, "invested": tied, "unrealized": unreal,
            "cash": cash, "value": cash + tied + unreal}


def today_stats(mode: str, day: str | None = None) -> dict:
    day = day or _today()
    pos = store.positions(mode)
    bought = [p for p in pos if p["day"] == day and p["status"] != "errore"]
    closed = [p for p in pos if p["closed_day"] == day and p["status"] == "chiusa"]
    return {"trades": len(bought), "pnl": sum(p["pnl"] or 0 for p in closed), "closed": len(closed),
            "open": sum(1 for p in pos if p["status"] in OPEN)}


def trade_size(cfg: dict, free: float) -> tuple[float, str]:
    """Importo della prossima operazione e, se 0, il motivo."""
    avail = free - float(cfg.get("auto_riserva") or 0)
    cost = avail * float(cfg.get("auto_percentuale") or 0) / 100
    cap = float(cfg.get("auto_importo_max") or 0)
    if cap > 0:
        cost = min(cost, cap)
    mn = float(cfg.get("auto_importo_min") or 0)
    if cost < mn:
        if avail >= mn:  # la percentuale darebbe troppo poco ma i soldi ci sono: si usa il minimo
            cost = mn
        else:
            return 0.0, f"liquidità insufficiente ({avail:.2f} {cfg.get('segnali_quote') or 'USDT'} disponibili, minimo {mn:.2f})"
    return math.floor(cost * 100) / 100, ""


def blocked_reason(cfg: dict, st: dict) -> str:
    if st["trades"] >= int(cfg.get("auto_max_trade_giorno") or 0):
        return f"limite di {cfg['auto_max_trade_giorno']} acquisti al giorno raggiunto"
    if st["open"] >= int(cfg.get("auto_max_posizioni") or 0):
        return f"già {st['open']} operazioni aperte (massimo {cfg['auto_max_posizioni']})"
    lim = float(cfg.get("auto_stop_perdita_giorno") or 0)
    if lim > 0 and st["pnl"] <= -lim:
        return f"perdita di oggi {st['pnl']:.2f} {cfg.get('segnali_quote') or 'USDT'} oltre il limite di {lim:.2f}: acquisti fermi fino a domani"
    return ""


# --------------------------------------------------------------------------- #
# Trader
# --------------------------------------------------------------------------- #

class AutoTrader:
    def __init__(self, notify, broker_factory=make_broker):
        self.notify = notify            # notify(cfg, level, title, text, short, symbol)
        self.broker_factory = broker_factory
        self._broker = None
        self._broker_key = None

    def broker(self, cfg: dict):
        key = (cfg.get("auto_modalita"), cfg.get("segnali_exchange"), store.kv_get("auto_keys_rev", 0))
        cache = self.__dict__.setdefault("_brokers", {})  # un broker per modalità (le reali si controllano sempre)
        if key not in cache:
            if len(cache) > 4:
                cache.clear()
            cache[key] = self.broker_factory(cfg)
        self._broker, self._broker_key = cache[key], key
        return cache[key]

    def _error(self, cfg: dict, what: str, e: Exception, symbol: str | None = None) -> None:
        msg = f"{what}: {type(e).__name__}: {e}"[:400]
        log.warning("investimento: %s", msg)
        hour = datetime.now().strftime("%Y-%m-%d %H")
        if store.add_error("tecnico", "investimento", msg, symbol=symbol, ref=f"auto|{what}|{symbol}|{hour}"):
            self.notify(cfg, "errore", "⚠️ Investimento automatico", msg, msg[:120], symbol)

    # -- nuovi segnali -------------------------------------------------------- #
    def on_signals(self, cfg: dict, rows: list[dict], closed_ms: int, now_ms: int | None = None) -> list[dict]:
        """Compra i segnali BUY massimo appena confermati (i più forti per primi), entro i limiti."""
        if not cfg.get("auto_attivo") or not rows:
            return []
        now_ms = now_ms or int(time.time() * 1000)
        if now_ms - closed_ms > tf_ms(cfg["segnali_timeframe"]) / 4:
            log.info("investimento: segnali di una candela chiusa da troppo tempo, non si compra")
            return []  # es. dopo un cambio di timeframe: il segnale è vecchio e il prezzo è già cambiato
        mode = cfg.get("auto_modalita", "simulazione")
        if mode not in MODES:
            return []
        if cfg.get("auto_solo_preavvisati"):
            rows = [r for r in rows if r.get("announced")]
        try:
            broker = self.broker(cfg)
        except Exception as e:  # noqa: BLE001
            self._error(cfg, "collegamento all'exchange", e)
            return []
        tf = cfg["segnali_timeframe"]
        bought = []
        for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
            p: dict | None = None
            sym = r["symbol"]
            buy, sell, stop = (float(r.get(k) or 0) for k in ("buy", "sell", "stop"))
            if not (buy > 0 and sell > buy > stop > 0):
                continue
            ref = f"{mode}|{sym}|{closed_ms}"
            if any(p["symbol"] == sym for p in store.positions(mode, OPEN)):
                continue  # già un'operazione aperta sulla coin
            why = blocked_reason(cfg, today_stats(mode))
            if why:
                self._skip(cfg, sym, why, closed_ms)
                break
            max_sp = float(cfg.get("auto_spread_max") or 0)
            if max_sp > 0 and hasattr(broker, "spread_pct"):
                try:
                    sp = broker.spread_pct(sym)
                except Exception:  # noqa: BLE001 - libro ordini non letto: si compra lo stesso col limite BUY
                    sp = None
                if sp is not None and sp > max_sp:
                    self._skip(cfg, sym, f"poco liquida: differenza fra acquisto e vendita {sp:.2f}% "
                                         f"(massimo {max_sp:g}%)", closed_ms)
                    continue
            try:
                free = broker.free_quote(cfg["segnali_quote"])
                cost, why = trade_size(cfg, free)
                if cost and cost < broker.min_cost(sym):
                    cost, why = 0, f"importo {cost:.2f} sotto il minimo dell'exchange"
                if not cost:
                    self._skip(cfg, sym, why, closed_ms)
                    break
                p = {"mode": mode, "status": "ordine", "symbol": sym, "ref": ref, "exchange": cfg["segnali_exchange"],
                     "timeframe": tf, "signal_close": closed_ms, "score": r.get("score"),
                     "probability": r.get("probability"), "buy": buy, "sell": sell, "stop": stop, "cost": cost,
                     "buy_expires": closed_ms + PLAN_FILL_BARS * tf_ms(tf), "horizon": plan_horizon(cfg, tf)}
                pid = store.add_position(p)
                if pid is None:
                    continue
                p["id"] = pid
                p["client_id"] = f"radar-{pid}-{closed_ms // 1000}"
                store.save_position(p)  # prima dell'invio: se il programma si ferma qui, l'ordine si ritrova
                order = broker.buy_limit(sym, cost, buy, client_id=p["client_id"])
                p.update(buy_order=order["id"], qty_ordered=order["qty"])
            except Exception as e:  # noqa: BLE001
                if p and p.get("id"):
                    if _network_error(e):  # l'ordine può essere arrivato: resta "ordine" e si cerca per id
                        p["error"] = str(e)[:300]
                        store.save_position(p)
                        bought.append(p)
                    else:
                        p.update(status="errore", error=str(e)[:300])
                        store.save_position(p)
                self._error(cfg, f"acquisto {sym}", e, sym)
                continue
            store.save_position(p)
            bought.append(p)
            tag = "🧪 SIMULAZIONE" if mode == "simulazione" else "💶 REALE"
            self.notify(cfg, "auto", f"🛒 Ordine BUY inviato – {sym} ({tag})",
                        f"{sym}: ordine BUY limite a {_p(buy)} per {cost:.2f} {cfg.get('segnali_quote') or 'USDT'} (valido {PLAN_FILL_BARS} candele "
                        f"{tf}).\nPoi: SELL {_p(sell)} · STOP {_p(stop)}.", f"{sym}: BUY {cost:.2f} {cfg.get('segnali_quote') or 'USDT'} a {_p(buy)}",
                        sym)
        return bought

    def _skip(self, cfg: dict, sym: str, why: str, closed_ms: int) -> None:
        key = f"auto_skip|{closed_ms}|{why[:40]}"
        if store.kv_get(key):
            return
        store.kv_set(key, True)
        self.notify(cfg, "auto", f"⏸️ Segnale {sym} non comprato", why, f"{sym} non comprato: {why}", sym)

    # -- controllo delle operazioni (ogni passo del motore) ------------------ #
    def tick(self, cfg: dict, now_ms: int | None = None) -> dict:
        now_ms = now_ms or int(time.time() * 1000)
        out = {"filled": [], "closed": [], "cancelled": []}
        cmd = store.kv_get("auto_command")
        if cmd:
            store.kv_set("auto_command", None)
        for mode in MODES:
            pos = store.positions(mode, OPEN)
            if cmd and cmd.get("action") == "close_all" and cmd.get("mode") == mode and pos:
                self._close_all(cfg, mode, pos, out)
                continue
            current = mode == cfg.get("auto_modalita", "simulazione")
            if not pos or (not current and mode != "reale"):
                continue  # le simulate restano ferme finché non torni alla simulazione
            try:  # le operazioni REALI si controllano sempre (STOP e SELL), anche se sei passato alla simulazione
                broker = self.broker(cfg) if current else self.broker({**cfg, "auto_modalita": mode})
            except Exception as e:  # noqa: BLE001
                self._error(cfg, "collegamento all'exchange", e)
                continue
            for p in pos:
                try:
                    self._check(cfg, broker, p, now_ms, out)
                except Exception as e:  # noqa: BLE001
                    self._error(cfg, f"controllo {p['symbol']}", e, p["symbol"])
        self.deposit_plan(cfg)
        self.snapshot(cfg)
        return out

    def _check(self, cfg: dict, broker, p: dict, now_ms: int, out: dict) -> None:
        sym = p["symbol"]
        if p["status"] == "ordine":
            if not p.get("buy_order"):  # risposta dell'exchange persa (rete, programma chiuso): si cerca per id
                o = broker.find_order(p["client_id"], sym) if p.get("client_id") else None
                if o:
                    p["buy_order"] = o["id"]
                    store.save_position(p)
                elif now_ms >= p["buy_expires"] + LOST_ORDER_GRACE_MS:
                    p.update(status="annullata", closed_day=_today(), pnl=0.0, cost=0.0,
                             reason="ordine BUY non arrivato all'exchange")
                    store.save_position(p)
                    out["cancelled"].append(sym)
                    return
                else:
                    return
            st = broker.buy_status(p)
            if st["filled"] > 0 and not st["done"] and now_ms < p["buy_expires"]:
                last = broker.last(sym)
                if last <= p["stop"]:  # comprato in parte e già allo STOP: si chiude subito la parte comprata
                    broker.cancel(sym, p.get("buy_order"))
                    self._opened(cfg, broker, p, broker.buy_status(p), now_ms)
                    self._closed(cfg, p, broker.sell_market(p, last), "STOP", out)
                return
            if st["filled"] > 0 and (st["done"] or now_ms >= p["buy_expires"]):
                if not st["done"]:  # eseguito in parte alla scadenza: si tiene la parte comprata
                    broker.cancel(sym, p.get("buy_order"))
                    st = broker.buy_status(p)
                self._opened(cfg, broker, p, st, now_ms)
                out["filled"].append(sym)
            elif st["done"] or now_ms >= p["buy_expires"]:
                broker.cancel(sym, p.get("buy_order"))
                st = broker.buy_status(p)  # può essersi eseguito proprio adesso, prima dell'annullamento
                if st["filled"] > 0:
                    self._opened(cfg, broker, p, st, now_ms)
                    out["filled"].append(sym)
                    return
                p.update(status="annullata", closed_day=_today(), pnl=0.0, cost=0.0,
                         reason="prezzo BUY non raggiunto in tempo")
                store.save_position(p)
                out["cancelled"].append(sym)
                self.notify(cfg, "auto", f"✖ Ordine BUY annullato – {sym}", f"{sym}: il prezzo non è sceso a "
                            f"{_p(p['buy'])} entro {PLAN_FILL_BARS} candele. Nessuna spesa.", f"{sym}: BUY annullato",
                            sym, push=False)
            return
        last = broker.last(sym)
        p["last"] = last
        st = broker.sell_status(p, last)
        if st["done"]:
            self._closed(cfg, p, st, "SELL raggiunto", out)
        elif last <= p["stop"]:
            self._closed(cfg, p, broker.sell_market(p, last), "STOP", out)
        elif now_ms >= p["expires"]:
            self._closed(cfg, p, broker.sell_market(p, last), "scadenza del piano", out)
        else:
            if not p.get("sell_order") or st.get("status") in DEAD_ORDER:
                # SELL mai inviato o annullato sull'exchange: si rimette, altrimenti l'obiettivo non arriverebbe mai
                if st.get("status") in DEAD_ORDER and st.get("filled"):
                    p["qty"] = max(p["qty"] - st["filled"], 0.0)
                try:
                    res = broker.replace_sell(p)
                    if res:
                        p["sell_order"], p["qty"] = res
                except Exception as e:  # noqa: BLE001 - riprova al prossimo controllo; lo STOP resta attivo
                    self._error(cfg, f"ordine SELL {sym}", e, sym)
            store.save_position(p)

    def _opened(self, cfg: dict, broker, p: dict, st: dict, now_ms: int) -> None:
        p.update(status="aperta", qty=st["filled"], entry=st["avg"], cost=round(st.get("cost") or p["cost"], 6),
                 filled_at=store.iso_utc(), expires=now_ms + p["horizon"] * tf_ms(p["timeframe"]))
        store.save_position(p)  # prima di inviare la vendita: se fallisce, la posizione resta registrata
        try:
            p["sell_order"] = broker.sell_limit(p["symbol"], p["qty"], p["sell"])
        except Exception as e:  # noqa: BLE001 - lo STOP e la scadenza vengono controllati comunque
            self._error(cfg, f"ordine SELL {p['symbol']}", e, p["symbol"])
        store.save_position(p)
        self.notify(cfg, "auto", f"✅ Comprato {p['symbol']}",
                    f"{p['symbol']}: comprati {p['qty']:.6g} a {_p(p['entry'])} ({p['cost']:.2f} {cfg.get('segnali_quote') or 'USDT'}).\n"
                    f"🎯 SELL {_p(p['sell'])} già impostato · 🛑 STOP {_p(p['stop'])} controllato dal radar.",
                    f"Comprato {p['symbol']} a {_p(p['entry'])}", p["symbol"])

    def _closed(self, cfg: dict, p: dict, st: dict, reason: str, out: dict) -> None:
        pnl = st["proceeds"] - p["cost"]
        p.update(status="chiusa", exit=st["avg"], proceeds=round(st["proceeds"], 6), pnl=round(pnl, 6),
                 pnl_pct=pnl / p["cost"] * 100 if p["cost"] else 0, reason=reason, closed_at=store.iso_utc(),
                 closed_day=_today())
        store.save_position(p)
        out["closed"].append(p["symbol"])
        icon = "🎯" if reason.startswith("SELL") else "🛑" if reason == "STOP" else "⏱️"
        self.notify(cfg, "auto", f"{icon} Venduto {p['symbol']} ({reason})",
                    f"{p['symbol']}: venduto a {_p(p['exit'])} · {pnl:+.2f} {cfg.get('segnali_quote') or 'USDT'} ({p['pnl_pct']:+.2f}%).",
                    f"{p['symbol']} {pnl:+.2f} {cfg.get('segnali_quote') or 'USDT'}", p["symbol"])
        if pnl < 0:
            store.add_error("previsione", "investimento", f"Operazione chiusa in perdita ({reason}): {pnl:+.2f} {cfg.get('segnali_quote') or 'USDT'}",
                            symbol=p["symbol"], ref=f"autoloss|{p['id']}")
        st_day = today_stats(p["mode"])
        lim = float(cfg.get("auto_stop_perdita_giorno") or 0)
        if lim > 0 and st_day["pnl"] <= -lim and not store.kv_get(f"auto_stopday|{_today()}"):
            store.kv_set(f"auto_stopday|{_today()}", True)
            self.notify(cfg, "errore", "⛔ Limite di perdita giornaliero raggiunto",
                        f"Oggi le operazioni chiuse perdono {st_day['pnl']:.2f} {cfg.get('segnali_quote') or 'USDT'}: fino a domani niente nuovi "
                        "acquisti. Le operazioni aperte restano protette da SELL e STOP.", "Acquisti fermi fino a domani")

    def _close_all(self, cfg: dict, mode: str, pos: list[dict], out: dict) -> None:
        try:
            broker = self.broker_factory({**cfg, "auto_modalita": mode})
        except Exception as e:  # noqa: BLE001
            self._error(cfg, "chiusura delle posizioni", e)
            return
        for p in pos:
            try:
                if p["status"] == "ordine":
                    broker.cancel(p["symbol"], p.get("buy_order"))
                    st = broker.buy_status(p)
                    if st["filled"] > 0:
                        self._opened(cfg, broker, p, st, int(time.time() * 1000))
                    else:
                        p.update(status="annullata", closed_day=_today(), pnl=0.0, cost=0.0, reason="annullato da te")
                        store.save_position(p)
                        continue
                last = broker.last(p["symbol"])
                self._closed(cfg, p, broker.sell_market(p, last), "chiusa da te", out)
            except Exception as e:  # noqa: BLE001
                self._error(cfg, f"chiusura {p['symbol']}", e, p["symbol"])

    # -- piano di versamento e storico --------------------------------------- #
    def deposit_plan(self, cfg: dict, today: date | None = None) -> bool:
        """Il giorno del versamento: in simulazione aggiunge il versamento virtuale, in reale manda un
        promemoria (i soldi li versi tu sull'exchange e il radar li investe con i segnali successivi)."""
        amount = float(cfg.get("auto_piano_importo") or 0)
        if amount <= 0:
            return False
        today = today or date.today()
        weekly = cfg.get("auto_piano_frequenza") == "settimanale"
        day = int(cfg.get("auto_piano_giorno") or 1)
        due = today.isoweekday() == min(max(day, 1), 7) if weekly else today.day == min(max(day, 1), 28)
        mode = cfg.get("auto_modalita", "simulazione")
        key = f"auto_plan|{mode}|{today.isoformat()}"
        if not due or store.kv_get(key):
            return False
        store.kv_set(key, True)
        if mode == "simulazione":
            store.add_deposit(mode, amount, "piano di versamento")
            self.notify(cfg, "auto", "💶 Versamento del piano (simulazione)",
                        f"Aggiunti {amount:.2f} {cfg.get('segnali_quote') or 'USDT'} virtuali: verranno investiti dai prossimi segnali.",
                        f"+{amount:.2f} {cfg.get('segnali_quote') or 'USDT'} virtuali", push=False)
        else:
            self.notify(cfg, "auto", "💶 Promemoria: versamento del piano",
                        f"Oggi è il giorno del versamento di {amount:.2f}. Versa sull'exchange e converti in "
                        f"{cfg['segnali_quote']}: il radar li investirà con i prossimi segnali (poi registralo nella "
                        "pagina Investimento automatico per le statistiche).", f"Versa {amount:.2f}")
        return True

    def snapshot(self, cfg: dict) -> None:
        last = store.kv_get("auto_snapshot_at", 0) or 0
        if time.time() - last < 600:
            return
        store.kv_set("auto_snapshot_at", time.time())
        for mode in MODES:
            if not store.deposits(mode) and not store.positions(mode, limit=1):
                continue
            w = wallet(mode)
            if mode == "reale":
                live = store.kv_get("auto_live_value")
                if live:
                    w["value"] = live["value"]
            store.set_wallet_day(_today(), mode, w["deposited"], round(w["value"], 2))


def _p(v) -> str:
    from webapp.engine import fmt_price

    return fmt_price(v)


# --------------------------------------------------------------------------- #
# Stato per l'interfaccia
# --------------------------------------------------------------------------- #

def status(cfg: dict, live_balance: bool = True) -> dict:
    mode = cfg.get("auto_modalita", "simulazione")
    w = wallet(mode)
    st = today_stats(mode)
    out = {"mode": mode, "active": bool(cfg.get("auto_attivo")), "wallet": w, "today": st,
           "blocked": blocked_reason(cfg, st), "keys": keys_public(), "exchange_free": None, "exchange_error": None}
    if mode == "reale" and live_balance and out["keys"]["configured"]:
        try:
            ex = private_exchange()
            bal = ex.fetch_balance()
            free = float((bal.get("free") or {}).get(cfg["segnali_quote"]) or 0)
            out["exchange_free"] = free
            value = free + sum(p.get("qty", 0) * (p.get("last") or p.get("entry") or 0)
                               for p in store.positions("reale", ("aperta",))) + \
                sum(p.get("cost") or 0 for p in store.positions("reale", ("ordine",)))
            store.kv_set("auto_live_value", {"value": value, "at": store.iso_utc()})
            out["exchange_value"] = value
        except Exception as e:  # noqa: BLE001
            out["exchange_error"] = f"{type(e).__name__}: {e}"[:300]
    free = out["exchange_free"] if mode == "reale" else w["cash"]
    out["next_size"], out["next_size_reason"] = trade_size(cfg, free) if free is not None else (0, "saldo non letto")
    return out


def history(mode: str) -> dict:
    pos = store.positions(mode)
    closed = [p for p in pos if p["status"] == "chiusa"]
    wins = [p for p in closed if (p["pnl"] or 0) > 0]
    return {
        "positions": pos,
        "deposits": store.deposits(mode),
        "days": store.wallet_days(mode),
        "summary": {"closed": len(closed), "wins": len(wins),
                    "win_rate": len(wins) / len(closed) * 100 if closed else None,
                    "pnl": sum(p["pnl"] or 0 for p in closed),
                    "best": max((p["pnl"] or 0 for p in closed), default=None),
                    "worst": min((p["pnl"] or 0 for p in closed), default=None)},
    }


def request_close_all(mode: str) -> None:
    store.kv_set("auto_command", {"action": "close_all", "mode": mode, "at": store.iso_utc()})


__all__ = ["AutoTrader", "PaperBroker", "LiveBroker", "wallet", "status", "history", "trade_size",
           "blocked_reason", "today_stats", "verify_keys", "save_keys", "load_keys", "keys_public", "delete_keys",
           "request_close_all", "MODES"]
