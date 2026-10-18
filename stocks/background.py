"""Processo in background (bot + controllo orario): avvio, arresto, avvio automatico con Windows."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from stocks.config import DATA_DIR

LOCK_PORT = 47615          # porta locale usata per garantire un solo bot alla volta
STOP_FILE = DATA_DIR / "bot.stop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "RadarUltimateBot"


def command() -> list[str]:
    """Comando che avvia il bot in background (senza finestra)."""
    if getattr(sys, "frozen", False):  # eseguibile creato con PyInstaller
        return [sys.executable, "--bot"]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parents[1] / "bot.py"
    return [str(pythonw if pythonw.exists() else exe), str(script)]


def command_line() -> str:
    return subprocess.list2cmdline(command())


# -- un solo bot alla volta --------------------------------------------------- #
def acquire_instance_lock() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=0.5):
            return True
    except OSError:
        return False


# -- avvio / arresto ---------------------------------------------------------- #
def start() -> None:
    clear_stop()
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command(), **kwargs)


def request_stop() -> None:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop", encoding="utf-8")


def stop_requested() -> bool:
    return STOP_FILE.exists()


def clear_stop() -> None:
    STOP_FILE.unlink(missing_ok=True)


# -- avvio automatico all'accesso a Windows (registro utente, niente permessi admin) -- #
def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("Avvio automatico disponibile solo su Windows.")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, command_line())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
