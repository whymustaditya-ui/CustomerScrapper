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

REM --- create virtual env on first run ------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [*] Pertama kali dijalankan - menyiapkan environment...
    !PY! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [X] Gagal membuat virtual environment.
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    echo [*] Menginstall dependencies ^(sekali saja, agak lama^)...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [X] Gagal install dependencies. Cek koneksi internet lalu jalankan lagi.
        pause
        exit /b 1
    )
    echo [*] Menginstall browser Chromium untuk scraping...
    python -m playwright install chromium
) else (
    call ".venv\Scripts\activate.bat"
)

echo.
echo [*] Membuka aplikasi di browser...
echo     Untuk berhenti: tutup jendela ini atau tekan Ctrl+C.
echo.

python -m streamlit run app.py

echo.
echo Aplikasi berhenti.
pause
