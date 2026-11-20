@echo off
REM ============================================================================
REM ONE-CLICK VRCHAT LAUNCHER (Your VRChat + Luna's VRChat + Luna's Brain)
REM ============================================================================
REM Double-click this file from anywhere. It automatically:
REM   1. Opens YOUR main VRChat window (Profile 0) if not already open
REM   2. Opens LUNA's small VRChat window (Profile 1, OSC port 9002) if not open
REM   3. Starts Luna's AI Brain (Vision Eyes + Voice + Mic Ears + Roaming)
REM ============================================================================
cd /d "%~dp0"
title Luna AI VTuber - One-Click VRChat Mode
python -u platforms\vrchat\vrchat_luna.py --listen %*
pause
