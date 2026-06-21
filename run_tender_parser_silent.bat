@echo off
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -r requirements.txt
)

call ".venv\Scripts\activate.bat"
echo [%date% %time%] Start >> "logs\daily.log"
python -m tender_parser run >> "logs\daily.log" 2>&1
echo [%date% %time%] Finish, exit code %errorlevel% >> "logs\daily.log"
