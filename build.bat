@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ==========================================================
rem  UnblurSub build script (PyInstaller onedir)
rem  Usage: double-click this file, or: build.bat
rem  NOTE: keep this file pure ASCII to avoid GBK decode issues
rem ==========================================================

set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"

echo ==========================================================
echo   UnblurSub build
echo ==========================================================
echo.

rem ---------- 1. locate a base python ----------
set "BASE_PY="
py -3 -c "import sys;print(sys.executable)" >nul 2>&1 && set "BASE_PY=py -3"
if not defined BASE_PY (
  where python >nul 2>&1 && set "BASE_PY=python"
)
if not defined BASE_PY (
  echo [ERROR] Python not found in PATH.
  echo         Install Python 3.10+ and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)
echo [1/6] Base python: %BASE_PY%

rem ---------- 2. create venv ----------
if exist "%VENV%\Scripts\python.exe" (
  echo [2/6] Reusing existing venv %VENV%
) else (
  echo [2/6] Creating venv %VENV% ...
  %BASE_PY% -m venv "%VENV%"
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    echo         Try: python -m venv --upgrade %VENV%
    echo.
    pause
    exit /b 1
  )
)

if not exist "%VPY%" (
  echo [ERROR] venv python not found: %VPY%
  echo.
  pause
  exit /b 1
)

rem ---------- 3. upgrade pip ----------
echo [3/6] Upgrading pip ...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 (
  echo [WARNING] pip upgrade failed, continue anyway.
)

rem ---------- 4. install deps ----------
echo [4/6] Installing dependencies ...
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
  echo.
  echo [ERROR] Dependency installation failed.
  echo         Check your network / proxy, then run again.
  echo.
  pause
  exit /b 1
)

rem ---------- 5. selftest ----------
echo [5/6] Running selftest ...
"%VPY%" selftest.py
if errorlevel 1 (
  echo.
  echo [ERROR] Selftest failed. Build aborted.
  echo.
  pause
  exit /b 1
)
echo Selftest passed.

rem ---------- 6. package ----------
echo [6/6] Packaging with PyInstaller ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

"%VPY%" -m PyInstaller --noconfirm --onedir --windowed --clean ^
  --name UnblurSub ^
  --add-data "web;web" ^
  --collect-submodules webview ^
  --hidden-import webview.platforms.edgechromium ^
  --exclude-module tkinter ^
  --exclude-module numpy ^
  --exclude-module matplotlib ^
  --exclude-module PIL ^
  app.py

if errorlevel 1 (
  echo.
  echo [ERROR] PyInstaller failed.
  echo.
  pause
  exit /b 1
)

echo.
echo ==========================================================
echo   BUILD OK
echo   Output: dist\UnblurSub\UnblurSub.exe
echo   Ship the whole dist\UnblurSub folder.
echo ==========================================================
echo.
pause
exit /b 0
