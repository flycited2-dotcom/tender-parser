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
if "%TENDER_PARSER_PROFILE%"=="" set "TENDER_PARSER_PROFILE=fast"
echo [%date% %time%] Start >> "logs\daily.log"
echo [%date% %time%] Profile %TENDER_PARSER_PROFILE% >> "logs\daily.log"
python -m tender_parser run --profile %TENDER_PARSER_PROFILE% >> "logs\daily.log" 2>&1
set "parser_exit_code=%errorlevel%"
echo [%date% %time%] Finish, exit code %parser_exit_code% >> "logs\daily.log"
exit /b %parser_exit_code%
