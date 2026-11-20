@echo off
REM Launch Luna in VRChat (Autonomous Mind + Body + OSC chatbox + Mic Listening + Dual Voice Output).
cd /d "%~dp0"
title Luna AI VTuber - Autonomous VRChat Companion
python -m pip install -q python-osc
python -u platforms\vrchat\vrchat_luna.py --listen %*
pause
