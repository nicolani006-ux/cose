"""Carica gli script delle skill day-trader da .claude/skills (i nomi delle cartelle hanno i trattini).

Adattato da tests/_load.py di day-trader-skill (J. Schincariol, MIT): qui gli script stanno in
.claude/skills/<skill>/ e i dati di prova in tests/day_trader/fixtures/.
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)), ".claude", "skills")
FIX = os.path.join(HERE, "fixtures")


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
