"""Pianificazione del controllo orario con l'Utilità di pianificazione di Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "Radar Ultimate - controllo azioni"


def task_command() -> str:
    if getattr(sys, "frozen", False):  # eseguibile creato con PyInstaller
        return f'"{sys.executable}" --monitor'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parents[1] / "monitor.py"
    return f'"{pythonw if pythonw.exists() else exe}" "{script}"'


def install_args(every_hours: int = 1) -> list[str]:
    return ["schtasks", "/Create", "/F", "/SC", "HOURLY", "/MO", str(every_hours),
            "/TN", TASK_NAME, "/TR", task_command()]


def _run(args: list[str]) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Disponibile solo su Windows."
    p = subprocess.run(args, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode == 0, (p.stdout or p.stderr).strip()


def install(every_hours: int = 1) -> tuple[bool, str]:
    return _run(install_args(every_hours))


def uninstall() -> tuple[bool, str]:
    return _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])


def is_installed() -> bool:
    return _run(["schtasks", "/Query", "/TN", TASK_NAME])[0]
