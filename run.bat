@echo off
setlocal
title LHC Transport Booking System
cd /d "%~dp0"
echo.
echo  Starting LHC Transport Booking System...
echo  Open http://127.0.0.1:5001 in your browser
echo.
if exist "C:\Program1\python.exe" (
  "C:\Program1\python.exe" app.py
  exit /b %errorlevel%
)
where py >nul 2>nul
if not errorlevel 1 (
  py app.py
  exit /b %errorlevel%
)
where python >nul 2>nul
if not errorlevel 1 (
  python app.py
  exit /b %errorlevel%
)
echo.
echo  Python was not found in PATH.
echo  Install Python 3.10+ and then run this script again.
pause
exit /b 1
