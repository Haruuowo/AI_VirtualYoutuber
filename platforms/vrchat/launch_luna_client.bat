@echo off
REM ============================================================================
REM Launch a dedicated 2nd VRChat Desktop window for LUNA's own VRChat account
REM ============================================================================
REM --profile=1   Gives Luna her own separate login session (Profile 0 = You)
REM --no-vr       Runs in lightweight desktop mode alongside your main game
REM --osc=9000... Connects OSC port 9000 directly to Luna's window
REM ============================================================================
title Launching Luna's VRChat Client (Profile 1)

set "VRC_EXE="
if defined VRC_INSTALL_PATH if exist "%VRC_INSTALL_PATH%\launch.exe" set "VRC_EXE=%VRC_INSTALL_PATH%\launch.exe"
if not defined VRC_EXE if exist "C:\Program Files (x86)\Steam\steamapps\common\VRChat\launch.exe" set "VRC_EXE=C:\Program Files (x86)\Steam\steamapps\common\VRChat\launch.exe"
if not defined VRC_EXE if exist "D:\SteamLibrary\steamapps\common\VRChat\launch.exe" set "VRC_EXE=D:\SteamLibrary\steamapps\common\VRChat\launch.exe"
if not defined VRC_EXE if exist "E:\SteamLibrary\steamapps\common\VRChat\launch.exe" set "VRC_EXE=E:\SteamLibrary\steamapps\common\VRChat\launch.exe"

if not defined VRC_EXE (
    echo [!] Could not auto-detect VRChat\launch.exe in default Steam paths.
    echo     Launching via Steam URI with Profile 1 flags...
    start "" "steam://rungameid/438100//--no-vr --profile=1 -screen-width 800 -screen-height 600 --fps=30 --osc=9000:127.0.0.1:9001/"
    exit /b 0
)

echo   Found VRChat: "%VRC_EXE%"
echo   Starting Luna's VRChat window (Profile 1, Desktop 800x600, OSC port 9000)...
start "" "%VRC_EXE%" --no-vr --profile=1 -screen-width 800 -screen-height 600 --fps=30 --osc=9000:127.0.0.1:9001
