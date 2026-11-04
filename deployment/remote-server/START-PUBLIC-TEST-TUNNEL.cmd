@echo off
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0START-PUBLIC-TEST-TUNNEL.ps1"
