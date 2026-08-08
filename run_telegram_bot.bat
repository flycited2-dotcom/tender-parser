@echo off
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
if not exist ".venv\Scripts\python.exe" exit /b 2

call ".venv\Scripts\activate.bat"
python -m tender_parser.telegram_bot >> "logs\telegram_bot.log" 2>&1
exit /b %errorlevel%
