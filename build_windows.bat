@echo off
REM Crea "dist\Crypto Radar Ultimate.exe"
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-build.txt || goto :error
pyinstaller --noconfirm crypto_radar.spec || goto :error
echo.
echo Fatto! Trovi leseguibile in: dist\Crypto Radar Ultimate.exe
pause
exit /b 0
:error
echo Errore durante la build.
pause
exit /b 1
