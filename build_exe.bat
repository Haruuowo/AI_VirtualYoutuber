@echo off
REM Build standalone LunaStudio.exe release using PyInstaller
cd /d "%~dp0"
title Building LunaStudio.exe Release...
echo.
echo ============================================================
echo         BUILDING LUNASTUDIO STANDALONE EXECUTABLE
echo ============================================================
echo.
python -m pip install -q pyinstaller
pyinstaller --clean --noconfirm LunaStudio.spec
echo.
if exist "dist\LunaStudio\LunaStudio.exe" (
    echo ============================================================
    echo [SUCCESS] Standalone EXE created at:
    echo           dist\LunaStudio\LunaStudio.exe
    echo ============================================================
    echo You can distribute the "dist\LunaStudio" folder as a zip!
) else (
    echo [ERROR] Build failed. Check the output above.
)
pause
