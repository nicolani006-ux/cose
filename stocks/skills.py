"""Accesso agli script delle skill di .claude/skills (day-trader-skill di J. Schincariol, licenza MIT).

Le cartelle hanno il trattino nel nome, quindi gli script si caricano dal percorso del file.
Nell'eseguibile per Windows sono inclusi in <app>/skills/.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CACHE: dict = {}


def skill_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parents[1] / ".claude" / "skills"]
    if hasattr(sys, "_MEIPASS"):
        roots.insert(0, Path(sys._MEIPASS) / "skills")
    return roots


def load_skill(folder: str, name: str):
    key = (folder, name)
    if key not in _CACHE:
        for root in skill_roots():
            path = root / folder / f"{name}.py"
            if path.exists():
                spec = importlib.util.spec_from_file_location(f"skill_{name}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _CACHE[key] = mod
                break
        else:
            raise ImportError(f"skill {folder}/{name}.py non trovata")
    return _CACHE[key]
