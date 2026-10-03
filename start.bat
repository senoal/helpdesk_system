@echo off
setlocal
cd /d "%~dp0"

rem Keep the portable launcher independent from stale machine-wide settings.
set "APP_ENV=development"
set "AUTO_CREATE_DB=1"
set "DATABASE_URL="
set "UPLOAD_FOLDER="
set "HOST=127.0.0.1"
set "PORT=8000"
set "PYTHONUTF8=1"

echo ==================================================
echo  ServiceHub - setup otomatis dan jalankan aplikasi
echo ==================================================

set "BOOTSTRAP_PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
if not defined BOOTSTRAP_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)
if not defined BOOTSTRAP_PYTHON (
    echo [ERROR] Python tidak ditemukan.
    echo Instal Python 3.10 atau lebih baru dari https://www.python.org/downloads/
    goto :failed
)

%BOOTSTRAP_PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] ServiceHub membutuhkan Python 3.10 atau lebih baru.
    %BOOTSTRAP_PYTHON% --version
    goto :failed
)

set "REBUILD_VENV=0"
if not exist ".venv\Scripts\python.exe" set "REBUILD_VENV=1"
if "%REBUILD_VENV%"=="0" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if errorlevel 1 set "REBUILD_VENV=1"
)

if "%REBUILD_VENV%"=="1" (
    echo [1/4] Membuat ulang virtual environment lokal...
    %BOOTSTRAP_PYTHON% -m venv --clear .venv
    if errorlevel 1 goto :failed
) else (
    echo [1/4] Virtual environment lokal siap.
)

echo [2/4] Memeriksa dan memasang dependensi...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto :failed

echo [3/4] Menyiapkan database dan data awal...
".venv\Scripts\python.exe" seed.py
if errorlevel 1 goto :failed

echo [4/4] ServiceHub berjalan di http://127.0.0.1:8000
echo Tekan Ctrl+C untuk menghentikan aplikasi.
".venv\Scripts\python.exe" wsgi.py
if errorlevel 1 goto :failed
goto :eof

:failed
echo.
echo ServiceHub gagal dijalankan. Periksa pesan error di atas.
echo Jalankan kembali start.bat setelah masalah diperbaiki.
pause
exit /b 1
