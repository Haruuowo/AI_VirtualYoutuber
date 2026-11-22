@echo off
REM ============================================================================
REM LUNA VRCHAT LAUNCHER (SteamVR 6-DOF Head + Hands Mode + Luna's AI Brain)
REM ============================================================================
REM How to use:
REM   1. Double-click this file (start_vrchat.bat) to start SteamVR with
REM      driver_luna (6-DOF Virtual Headset + Left/Right Hands) and open Luna's
REM      VRChat window (Profile 1, Friends+ Home Room, OSC port 9002).
REM   2. In YOUR main VRChat (start_my_vrchat.bat), open your Social menu,
REM      click on kairu!, and click "Join" — no invite needed!
REM ============================================================================
cd /d "%~dp0"
title Luna AI VTuber - 6-DOF VRChat Companion (Profile 1)
python -u platforms\vrchat\vrchat_luna.py --vr --listen %*
pause
