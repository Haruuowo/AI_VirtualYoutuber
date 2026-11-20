@echo off
REM ============================================================================
REM Open YOUR Main VRChat window (Profile 0 = DumAlcoholicPa3, OSC 9000)
REM even when Steam's button shows "STOP" because Luna's window is already open!
REM ============================================================================
title Your Main VRChat Window (Profile 0)

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

echo   Starting YOUR Main VRChat window (Profile 0) from "%VRC_DIR%"...
start "" /D "%VRC_DIR%" "%VRC_DIR%\launch.exe" --profile=0 --no-vr
