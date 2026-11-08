"""Invio degli avvisi: Telegram (arriva sul telefono) e notifica del desktop."""

from __future__ import annotations

import requests

TELEGRAM_LIMIT = 4000


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip("\n"))
    return parts


def send_telegram(token: str, chat_id: str, text: str, session=None) -> None:
    s = session or requests
    for part in split_message(text):
        r = s.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()


def desktop_notify(title: str, message: str) -> None:
    from plyer import notification

    notification.notify(title=title[:63], message=message[:250], app_name="Radar Ultimate", timeout=15)


def notify_all(cfg: dict, title: str, text: str, short: str) -> list[str]:
    """Invia su tutti i canali configurati; ritorna l'esito per ciascuno."""
    status = []
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        try:
            send_telegram(cfg["telegram_token"], str(cfg["telegram_chat_id"]), f"{title}\n\n{text}")
            status.append("Telegram: inviato")
        except Exception as e:  # noqa: BLE001
            status.append(f"Telegram: errore ({e})")
    if cfg.get("notifiche_desktop"):
        try:
            desktop_notify(title, short)
            status.append("Desktop: inviato")
        except Exception as e:  # noqa: BLE001
            status.append(f"Desktop: non disponibile ({e})")
    return status


def find_chat_ids(token: str, session=None, code: str | None = None) -> list[str]:
    """Chat ID di chi ha scritto al bot (serve per configurare Telegram). Con `code` solo chi
    ha scritto il codice di collegamento."""
    s = session or requests
    r = s.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    r.raise_for_status()
    out = []
    for u in r.json().get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        chat = m.get("chat", {})
        if code and code.upper() not in (m.get("text") or "").upper():
            continue
        if chat.get("id") is not None:
            label = f"{chat['id']} ({chat.get('first_name') or chat.get('title') or ''})"
            if label not in out:
                out.append(label)
    return out
