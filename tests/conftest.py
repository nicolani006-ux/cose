import os
import sys
import tempfile
from pathlib import Path

# Dati e configurazione dei test in una cartella temporanea, mai in quella dell'utente.
os.environ["RADAR_HOME"] = tempfile.mkdtemp(prefix="radar_test_")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
