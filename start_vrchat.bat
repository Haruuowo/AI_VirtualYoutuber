@echo off
REM ============================================================================
REM LUNA VRCHAT LAUNCHER (SteamVR 6-DOF Head + Hands Mode + Luna's AI Brain)
REM ============================================================================
REM How to use:
REM   1. Double-click start_my_vrchat.bat to open YOUR main VRChat in Desktop
REM      Mode (--profile=0 --no-vr).
REM   2. Double-click this file (start_vrchat.bat) to start SteamVR with
REM      driver_luna (Virtual 6-DOF Headset + Left/Right Hands), open Luna's
REM      VRChat window (Profile 1, OSC port 9002) in VR Mode, and start her Brain!
REM ============================================================================
cd /d "%~dp0"
title Luna AI VTuber - 6-DOF VRChat Companion (Profile 1)
python -u platforms\vrchat\vrchat_luna.py --vr --listen %*
pause
