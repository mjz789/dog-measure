@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

where uv >nul 2>nul
if not errorlevel 1 goto use_uv

where py >nul 2>nul
if not errorlevel 1 goto use_py

where python >nul 2>nul
if not errorlevel 1 goto use_python

echo [狗狗视觉测量] Python 3.11+ was not found.
echo Install Python from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1

:use_uv
set "UV_CACHE_DIR=%CD%\.uv-cache"
set "UV_PYTHON_INSTALL_DIR=%CD%\.uv-python"
uv venv --python 3.12 .venv
if errorlevel 1 goto failed
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto failed
goto run

:use_py
py -3.12 -m venv .venv 2>nul
if errorlevel 1 py -3.11 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
goto run

:use_python
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
exit /b %errorlevel%

:failed
echo.
echo [狗狗视觉测量] Environment setup failed. Check the network connection and disk permissions.
pause
exit /b 1
