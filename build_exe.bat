@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [狗狗视觉测量] PyInstaller is not installed in .venv.
    echo Run: uv pip install --python .venv\Scripts\python.exe pyinstaller
    pause
    exit /b 1
)

".venv\Scripts\pyinstaller.exe" --noconfirm --clean PlaneVision.spec
if errorlevel 1 (
    echo.
    echo [狗狗视觉测量] EXE build failed.
    pause
    exit /b 1
)

echo.
echo [狗狗视觉测量] Build complete: dist\狗狗视觉测量.exe
pause
