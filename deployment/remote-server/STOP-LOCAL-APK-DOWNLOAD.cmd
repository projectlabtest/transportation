@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0STOP-LOCAL-APK-DOWNLOAD.ps1"
endlocal
