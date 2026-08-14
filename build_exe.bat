@echo off
setlocal
cd /d "%~dp0"
set "PYINSTALLER_CONFIG_DIR=%CD%\.pyinstaller-cache"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [狗狗视觉测量] PyInstaller is not installed in .venv.
    echo Run: uv pip install --python .venv\Scripts\python.exe pyinstaller
    pause
    exit /b 1
)

if not defined UPX_DIR (
    for /d %%D in ("tools\upx-*-win64") do set "UPX_DIR=%%~fD"
)

if defined UPX_DIR if exist "%UPX_DIR%\upx.exe" (
    echo [狗狗视觉测量] Compact build with UPX: %UPX_DIR%
    ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --upx-dir "%UPX_DIR%" PlaneVision.spec
) else (
    echo [狗狗视觉测量] UPX was not found. Building the standard portable EXE.
    echo Set UPX_DIR or extract UPX for Windows to tools\upx-VERSION-win64 for a smaller EXE.
    ".venv\Scripts\pyinstaller.exe" --noconfirm --clean PlaneVision.spec
)
if errorlevel 1 (
    echo.
    echo [狗狗视觉测量] EXE build failed.
    pause
    exit /b 1
)

echo.
echo [狗狗视觉测量] Build complete: dist\狗狗视觉测量.exe
pause
