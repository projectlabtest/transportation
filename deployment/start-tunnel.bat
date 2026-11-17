@echo off
REM Starts the Cloudflare tunnel and keeps the Worker + public URL in sync.
REM Usage: start-tunnel.bat <CF_API_TOKEN>
setlocal
set "TOKEN=%~1"
if "%TOKEN%"=="" set "TOKEN=%CF_TOKEN%"
if "%TOKEN%"=="" (
    echo ERROR: No Cloudflare API token.
    echo Usage: start-tunnel.bat YOUR_TOKEN
    pause
    exit /b 1
)
cd /d "%~dp0"
python run-tunnel.py "%TOKEN%" transport-booking.duckdns.org
pause
