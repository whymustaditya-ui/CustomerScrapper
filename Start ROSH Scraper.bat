@echo off
REM ============================================================
REM  ROSH Super Customer Scraper - one-click launcher
REM  Double-click this file. No need to type anything in cmd.
REM ============================================================
setlocal enabledelayedexpansion
title ROSH Customer Scraper
cd /d "%~dp0"

echo.
echo ==========================================
echo   ROSH Super Customer Scraper
echo ==========================================
echo.

REM --- find a usable Python (prefer 3.12, avoid bleeding-edge 3.14) ---
set "PY="
for %%I in ("py -3.12" "py -3.13" "py -3" "python") do (
    if not defined PY (
        %%~I --version >nul 2>nul
        if !errorlevel!==0 set "PY=%%~I"
    )
)

if not defined PY (
    echo [X] Python tidak ditemukan. Install dulu dari https://python.org
    echo     dan centang "Add Python to PATH" saat install.
    echo.
    pause
    exit /b 1
)
echo [*] Pakai Python: !PY!

REM --- ensure a healthy virtual env ---------------------------------
REM  Rebuild if missing OR corrupted. OneDrive can dehydrate files in
REM  .venv and break the install (streamlit import fails), so we verify
REM  the env actually works instead of just checking that the folder exists.
set "NEED_SETUP="
if not exist ".venv\Scripts\python.exe" set "NEED_SETUP=1"
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -c "import streamlit" >nul 2>nul || set "NEED_SETUP=1"

if defined NEED_SETUP (
    echo [*] Menyiapkan / memperbaiki environment ^(sekali saja, agak lama^)...
    if exist ".venv" rmdir /s /q ".venv"
    !PY! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [X] Gagal membuat virtual environment.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [X] Gagal install dependencies. Cek koneksi internet lalu jalankan lagi.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

REM --- pastikan browser Chromium untuk Playwright tersedia (self-heal) ---
REM  Idempotent: kalau sudah ada, ini cepat; kalau hilang, langsung dipasang.
echo [*] Memastikan browser Chromium siap...
python -c "from pathlib import Path; import sys; sys.exit(0 if any(Path.home().glob('AppData/Local/ms-playwright/chromium-*')) else 1)" >nul 2>nul || python -m playwright install chromium

echo.
echo [*] Membuka aplikasi di browser...
echo     Untuk berhenti: tutup jendela ini atau tekan Ctrl+C.
echo.

python -m streamlit run app.py

echo.
echo Aplikasi berhenti.
pause
