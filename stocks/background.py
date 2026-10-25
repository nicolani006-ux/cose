"""Processo in background (bot + controllo orario): avvio, arresto, avvio automatico con Windows."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from stocks.config import DATA_DIR

LOCK_PORT = 47615          # porta locale usata per garantire un solo bot alla volta
STOP_FILE = DATA_DIR / "bot.stop"
STARTING_FILE = DATA_DIR / "bot.avvio"
STATUS_FILE = DATA_DIR / "bot_stato_processo.json"
LOG_FILE = DATA_DIR / "bot.log"
STARTUP_SECONDS = 120  # un eseguibile "onefile" può metterci anche un minuto a partire (antivirus)
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
def start() -> int:
    """Avvia il bot come processo indipendente (resta acceso anche chiudendo l'app). Ritorna il PID."""
    clear_stop()
    STARTING_FILE.parent.mkdir(parents=True, exist_ok=True)
    STARTING_FILE.write_text(str(time.time()), encoding="utf-8")
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # Altrimenti il nuovo processo userebbe i file temporanei dell'app e morirebbe chiudendola.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "close_fds": True, "env": env, "cwd": str(Path.home())}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command(), **kwargs).pid


def starting() -> bool:
    """Avvio richiesto da poco e bot non ancora pronto."""
    try:
        since = float(STARTING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return time.time() - since < STARTUP_SECONDS and not is_running()


def wait_until_running(timeout: float = STARTUP_SECONDS, step: float = 1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if is_running():
            return True
        time.sleep(step)
    return is_running()


# -- stato del processo (scritto dal bot, letto dall'app) --------------------- #
def write_status(**fields) -> None:
    data = read_status()
    data.update(fields, aggiornato=datetime.now().isoformat(timespec="seconds"))
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except OSError:
        pass


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def log_tail(lines: int = 40) -> str:
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def log_crash(text: str) -> None:
    """Scrive un errore nel registro anche se il bot muore prima di aver configurato il logging."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} ERRORE AVVIO\n{text}\n")
    except OSError:
        pass


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
