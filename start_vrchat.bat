@echo off
REM ============================================================================
REM LUNA VRCHAT LAUNCHER (Luna's Profile 1 Window + Luna's AI Brain)
REM ============================================================================
REM How to use:
REM   1. Double-click start_my_vrchat.bat to open YOUR main VRChat (Profile 0)
REM      into the shared Friends+ room.
REM   2. Double-click this file (start_vrchat.bat) to open Luna's VRChat window
REM      (Profile 1, OSC port 9002) directly into your room and start her Brain!
REM ============================================================================
cd /d "%~dp0"
title Luna AI VTuber - VRChat Companion (Profile 1)
python -u platforms\vrchat\vrchat_luna.py --listen %*
pause
