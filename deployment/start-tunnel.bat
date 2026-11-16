@echo off
REM ============================================
REM Cloudflare Quick Tunnel for Transport Booking
REM No domain required - gives a free HTTPS URL
REM ============================================
echo.
echo  Starting Cloudflare Tunnel...
echo  The public HTTPS URL will be shown below.
echo  Copy it and set it in: Gear (gear icon) -> QR CODE SETTINGS -> Public URL
echo.

where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: cloudflared not found.
    echo  Download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    echo  Place cloudflared.exe in this folder or add to PATH.
    pause
    exit /b 1
)

cloudflared tunnel --url http://localhost:5019
pause