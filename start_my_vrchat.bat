@echo off
REM ============================================================================
REM OPEN YOUR MAIN VRCHAT ACCOUNT (Profile 0 = DumAlcoholicPa3, OSC Port 9000)
REM ============================================================================
REM Launches your main VRChat in Desktop Mode (--no-vr) directly into a shared
REM Friends+ (~hidden) instance of your Home world so Luna spawns right next to
REM you automatically without needing any invite!
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

set "SHARED_WORLD=vrchat://launch?id=wrld_79e079c1-9548-48e7-a16e-e4836e1fd922:77777~hidden(usr_2df6f681-876a-43d1-a3ca-4886540811fe)~region(jp)"

echo   Opening YOUR Main VRChat window (Profile 0 - DumAlcoholicPa3) in Shared Friends+ Room...
start "" /D "%VRC_DIR%" "%VRC_DIR%\launch.exe" --profile=0 --no-vr "%SHARED_WORLD%"
