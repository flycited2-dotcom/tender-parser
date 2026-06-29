@echo off
setlocal
cd /d "%~dp0"

if not exist "browser_profiles" mkdir "browser_profiles"

set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" (
    echo Google Chrome not found. Install Chrome or update CHROME_EXE in this file.
    pause
    exit /b 1
)

start "" "%CHROME_EXE%" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%CD%\browser_profiles\rts_chrome" "https://223.rts-tender.ru/"
