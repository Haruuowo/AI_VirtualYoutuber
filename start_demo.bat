@echo off
REM One-click Luna interview demo: installs what it needs, starts the server, opens the browser.
cd /d "%~dp0"
title Luna AI VTuber - Demo
echo.
echo   Starting Luna demo...
python -m pip install -q -r demo\requirements.txt
if not exist .env (
  echo   [!] No .env found. Copy .env.example to .env and add GROQ_API_KEY for the live AI brain.
  echo       Luna will still run with a scripted fallback brain.
)
python demo\server.py
pause

