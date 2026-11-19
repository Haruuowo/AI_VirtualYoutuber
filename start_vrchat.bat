@echo off
REM Launch Luna in VRChat (OSC chatbox + voice through a virtual cable).
cd /d "%~dp0"
title Luna AI VTuber - VRChat
python -m pip install -q python-osc
python platforms\vrchat\vrchat_luna.py %*
pause
