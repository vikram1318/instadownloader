@echo off
setlocal enabledelayedexpansion
title InstaBatch Downloader

echo.
echo  ============================================================
echo    InstaBatch Downloader  ^|  Web UI
echo  ============================================================
echo.

REM ── Move to the folder where run.bat lives ────────────────────
cd /d "%~dp0"

REM ── Check Python is installed ─────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Please install Python 3.8+ from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER% found.

REM ── Virtual environment (optional fallback) ─────────────────
if not exist "venv\Scripts\activate.bat" (
    echo  [*] Setting up environment...
    python -m venv venv >nul 2>&1
)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM ── Install / update packages ────────────────────────────────
echo  [*] Checking packages (flask, yt-dlp, imageio-ffmpeg)...
pip install flask yt-dlp imageio-ffmpeg -q
echo  [OK] Packages ready.

REM ── Check ffmpeg (optional but recommended) ───────────────────
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] ffmpeg not found in PATH.
    echo         Videos will still download but audio+video may not merge.
    echo         Install ffmpeg: https://ffmpeg.org/download.html
    echo.
) else (
    echo  [OK] ffmpeg found.
)

REM ── Launch app & open browser ────────────────────────────────
echo.
echo  ============================================================
echo    Opening browser at:  http://localhost:5050
echo    Press Ctrl+C in this window to stop the server.
echo  ============================================================
echo.

start "" http://localhost:5050
python app.py

echo.
echo  [*] Server stopped.
pause
