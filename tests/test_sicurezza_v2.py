"""Token cifrato su disco e codice di collegamento del bot."""

import json
import os


from stocks import secret
from stocks.config import CONFIG_FILE, DEFAULT_CONFIG, load_config, pairing_code, save_config


def test_token_roundtrip_and_never_plain_on_windows():
    save_config({**DEFAULT_CONFIG, "telegram_token": "123456:SEGRETO"})
    assert load_config()["telegram_token"] == "123456:SEGRETO"
    on_disk = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["telegram_token"]
    if os.name == "nt":
        assert on_disk.startswith(secret.PREFIX) and "SEGRETO" not in on_disk
    save_config(dict(DEFAULT_CONFIG))


def test_unreadable_token_becomes_empty():
    assert secret.unprotect("") == "" and secret.unprotect("in-chiaro") == "in-chiaro"
    assert secret.unprotect(secret.PREFIX + "bm9uLXZhbGlkbw==") == ""  # copiato da un altro PC


def test_env_token_wins(monkeypatch):
    monkeypatch.setenv("RADAR_TELEGRAM_TOKEN", "DA-ENV")
    assert load_config()["telegram_token"] == "DA-ENV"


def test_pairing_code_is_stable():
    save_config(dict(DEFAULT_CONFIG))
    code = pairing_code()
    assert code == pairing_code() == load_config()["codice_collegamento"]
    save_config(dict(DEFAULT_CONFIG))


def test_find_chat_ids_filters_by_code():
    from stocks import notify

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [{"message": {"text": "/start ABCD1234", "chat": {"id": 1, "first_name": "Io"}}},
                               {"message": {"text": "/start", "chat": {"id": 2, "first_name": "Altro"}}}]}

    class S:
        def get(self, *a, **k):
            return R()

    assert notify.find_chat_ids("T", session=S(), code="abcd1234") == ["1 (Io)"]
