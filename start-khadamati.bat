@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\SOOQ ELASER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if exist "%PYTHON_EXE%" (
  set "RUNNER=%PYTHON_EXE%"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "RUNNER=py"
  ) else (
    where python >nul 2>nul
    if not errorlevel 1 set "RUNNER=python"
  )
)

if "%RUNNER%"=="" (
  echo Python was not found.
  echo Install Python, then run this file again.
  pause
  exit /b 1
)

start "Khadamati Server - keep this window open" cmd /k "%RUNNER% server.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8080"

echo Khadamati is opening at http://127.0.0.1:8080
echo Keep the server window open while using the site.
timeout /t 4 /nobreak >nul
