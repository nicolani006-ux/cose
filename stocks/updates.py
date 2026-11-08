"""Controllo degli aggiornamenti: confronta la versione dell'app con l'ultima release su GitHub."""

from __future__ import annotations

import re

from version import REPO, VERSION

API = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def latest_release(session=None) -> dict | None:
    """{"version", "url", "notes"} dell'ultima release, None se non raggiungibile (es. repository privato)."""
    from stocks.sources import http_get

    try:
        data = http_get(API, session=session, timeout=10, retries=1).json()
    except Exception:  # noqa: BLE001
        return None
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    return {"version": tag.lstrip("vV"), "url": data.get("html_url") or f"https://github.com/{REPO}/releases",
            "notes": (data.get("body") or "")[:1500]}


def update_available(session=None) -> dict | None:
    rel = latest_release(session)
    if rel and parse_version(rel["version"]) > parse_version(VERSION):
        return rel
    return None
