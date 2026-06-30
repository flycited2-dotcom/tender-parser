@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\Setup_Happ_RTS_Direct.ps1"
pause
