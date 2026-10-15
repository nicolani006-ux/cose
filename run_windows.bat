@echo off
REM Avvia lapp da sorgente (serve Python 3.10+ installato)
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python launcher.py
