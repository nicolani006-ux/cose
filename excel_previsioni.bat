@echo off
REM Aggiorna il file Excel del test delle previsioni (doppio clic)
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements-previsioni.txt
python excel_previsioni.py %*
pause
