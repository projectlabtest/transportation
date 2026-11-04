@echo off
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0REPORT-PUBLIC-TEST-URL.ps1"
