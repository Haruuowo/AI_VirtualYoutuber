@echo off
REM ============================================================================
REM Open a SECOND VRChat window for LUNA's account on this same PC.
REM   - Your normal VRChat (Steam Play button) = Profile 0 = your account, OSC 9000
REM   - This window                           = Profile 1 = Luna's account, OSC 9002
REM Steam refuses to start a game twice, so this runs VRChat's launch.exe directly
REM (the same way the VRChat SDK opens extra test clients).
REM ============================================================================
title Luna's VRChat window (Profile 1, OSC 9002)

set "ARGS=--profile=1 --no-vr -screen-width 854 -screen-height 480 -screen-fullscreen 0 --fps=30 --osc=9002:127.0.0.1:9003"

set "VRC_DIR="
if defined VRC_INSTALL_PATH if exist "%VRC_INSTALL_PATH%\launch.exe" set "VRC_DIR=%VRC_INSTALL_PATH%"
for %%D in ("D:\SteamLibrary\steamapps\common\VRChat" "C:\Program Files (x86)\Steam\steamapps\common\VRChat" "E:\SteamLibrary\steamapps\common\VRChat") do (
    if not defined VRC_DIR if exist "%%~D\launch.exe" set "VRC_DIR=%%~D"
)
if not defined VRC_DIR (
    echo [!] VRChat folder not found. Set VRC_INSTALL_PATH to your VRChat install folder.
    pause
    exit /b 1
)

echo   Starting Luna's VRChat window from "%VRC_DIR%"
start "" /D "%VRC_DIR%" "%VRC_DIR%\launch.exe" %ARGS%
echo.
echo   First time only: log in with LUNA's VRChat account in the new small window.
echo   In Luna's window: Mic = "CABLE Output", OSC = Enabled.
echo   Then mute Luna's VRChat window in the Windows Volume Mixer (avoids double audio).
timeout /t 8 >nul
