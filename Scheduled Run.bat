@echo off
REM ============================================================
REM  ROSH Scraper - scheduled headless batch runner
REM  Meant for Windows Task Scheduler (weekdays 05:20). Runs the
REM  scraper without the Streamlit UI, releases the next gated
REM  batch, and logs to data\output\scheduled_run.log.
REM
REM  Change the lead count by editing the --size value below.
REM  Run ONLY on a home/business network, never DJP/MoF.
REM ============================================================
setlocal enabledelayedexpansion
title ROSH Scraper - scheduled run
cd /d "%~dp0"

REM --- find a usable Python (prefer 3.12, avoid bleeding-edge 3.14) ---
set "PY="
for %%I in ("py -3.12" "py -3.13" "py -3" "python") do (
    if not defined PY (
        %%~I --version >nul 2>nul
        if !errorlevel!==0 set "PY=%%~I"
    )
)
if not defined PY (
    echo [X] Python tidak ditemukan. Install dari https://python.org (centang "Add to PATH").
    exit /b 1
)

REM --- ensure a healthy virtual env (same location as the launcher) ---
REM  Lives in %LOCALAPPDATA% so OneDrive never syncs/dehydrates it.
set "VENV=%LOCALAPPDATA%\ROSH-Scraper\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "NEED_SETUP="
if not exist "%VPY%" set "NEED_SETUP=1"
if exist "%VPY%" "%VPY%" -c "import streamlit" >nul 2>nul || set "NEED_SETUP=1"

if defined NEED_SETUP (
    echo [*] Menyiapkan environment (sekali saja)...
    if exist "%VENV%" rmdir /s /q "%VENV%"
    !PY! -m venv "%VENV%"
    if not exist "%VPY%" ( echo [X] Gagal membuat venv. & exit /b 1 )
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 ( echo [X] Gagal install dependencies. & exit /b 1 )
)

REM --- ensure Chromium for Playwright is present (idempotent) ---
"%VPY%" -c "from pathlib import Path; import sys; sys.exit(0 if any(Path.home().glob('AppData/Local/ms-playwright/chromium-*')) else 1)" >nul 2>nul || "%VPY%" -m playwright install chromium

echo [*] Menjalankan batch terjadwal...
"%VPY%" run_batch.py --size 10

echo [*] Selesai. Lihat data\output\scheduled_run.log untuk hasil.
endlocal
