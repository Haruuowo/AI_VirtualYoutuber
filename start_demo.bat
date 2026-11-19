@echo off
REM One-click Luna browser demo (Live2D stage + chat).
cd /d "%~dp0"
title Luna AI VTuber - Web Stage
echo.
echo   Starting Luna web stage...
python -m pip install -q -r platforms\web_stage\requirements.txt
if not exist .env (
  echo   [!] No .env found. Copy .env.example to .env and add GROQ_API_KEY for the live AI brain.
  echo       Luna will still run with a scripted fallback brain.
)
python platforms\web_stage\server.py
pause
