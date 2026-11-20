@echo off
REM ============================================================================
REM LUNA VRCHAT LAUNCHER (Luna's Profile 1 Window + Luna's AI Brain)
REM ============================================================================
REM How to use:
REM   1. Open YOUR own VRChat from Steam first and load in.
REM   2. Double-click this file (start_vrchat.bat) to launch Luna's separate
REM      VRChat window (Profile 1, OSC port 9002) and her AI Brain!
REM ============================================================================
cd /d "%~dp0"
title Luna AI VTuber - VRChat Companion (Profile 1)
python -u platforms\vrchat\vrchat_luna.py --listen %*
pause
