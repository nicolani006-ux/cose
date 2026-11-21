@echo off
REM Avvia Radar Web (app locale nel browser) da sorgente. Serve Python 3.10+ installato.
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q --disable-pip-version-check -r requirements.txt -c constraints.txt
python radar_web.py
pause
