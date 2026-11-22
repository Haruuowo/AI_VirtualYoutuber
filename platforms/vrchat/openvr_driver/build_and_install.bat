@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
cl /nologo /O2 /EHsc /std:c++17 /LD driver_luna.cpp /Fe:driver_luna.dll ws2_32.lib
if errorlevel 1 exit /b 1

echo [build] Successfully compiled driver_luna.dll!
