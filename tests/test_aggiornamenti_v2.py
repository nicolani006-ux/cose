"""Versione, controllo aggiornamenti e autotest dell'eseguibile."""

import subprocess
import sys
from pathlib import Path

from stocks import updates
from version import VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_parse_version():
    assert updates.parse_version("v2.10.1") > updates.parse_version("2.9.9")
    assert updates.parse_version("") == (0,)


def test_update_available(monkeypatch):
    monkeypatch.setattr(updates, "latest_release", lambda session=None: {"version": "99.0.0", "url": "u", "notes": ""})
    assert updates.update_available()["version"] == "99.0.0"
    monkeypatch.setattr(updates, "latest_release", lambda session=None: {"version": VERSION, "url": "u", "notes": ""})
    assert updates.update_available() is None
    monkeypatch.setattr(updates, "latest_release", lambda session=None: None)
    assert updates.update_available() is None


def test_bot_notifies_new_version_once(monkeypatch):
    from stocks import telegram_bot as tb

    sent = []
    monkeypatch.setattr(updates, "latest_release", lambda session=None: {"version": "99.0.0", "url": "u", "notes": ""})
    monkeypatch.setattr(tb, "notify_all", lambda cfg, title, text, short: sent.append(text))
    tb.BOT_STATE.unlink(missing_ok=True)
    bot = tb.RadarBot(None)
    bot.check_updates()
    bot.check_updates(every_s=0)  # stessa versione: nessun secondo avviso
    assert len(sent) == 1 and "99.0.0" in sent[0]


def test_launcher_version_and_self_test():
    out = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "--versione"], capture_output=True, text=True,
                         timeout=60)
    assert out.stdout.strip() == VERSION
    res = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "--self-test"], capture_output=True, text=True,
                         timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
