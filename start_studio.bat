@echo off
REM Launch the AI VTuber Studio Desktop App
cd /d "%~dp0"
title AI VTuber Studio
start "" pythonw platforms\desktop_studio\app.py || python platforms\desktop_studio\app.py
