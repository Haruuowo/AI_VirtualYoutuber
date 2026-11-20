@echo off
REM ============================================================================
REM OPEN YOUR MAIN VRCHAT ACCOUNT (Profile 0 = DumAlcoholicPa3, OSC Port 9000)
REM ============================================================================
REM Works anytime — even when Steam's button shows "STOP" because Luna is open!
REM ============================================================================
title Starting YOUR Main VRChat (Profile 0)

set "VRC_DIR="
if defined VRC_INSTALL_PATH if exist "%VRC_INSTALL_PATH%\launch.exe" set "VRC_DIR=%VRC_INSTALL_PATH%"
for %%D in ("D:\SteamLibrary\steamapps\common\VRChat" "C:\Program Files (x86)\Steam\steamapps\common\VRChat" "E:\SteamLibrary\steamapps\common\VRChat") do (
    if not defined VRC_DIR if exist "%%~D\launch.exe" set "VRC_DIR=%%~D"
)
if not defined VRC_DIR (
    echo [!] VRChat folder not found.
    pause
    exit /b 1
)

echo   Opening YOUR Main VRChat window (Profile 0 - DumAlcoholicPa3)...
start "" /D "%VRC_DIR%" "%VRC_DIR%\launch.exe" --profile=0 --no-vr
