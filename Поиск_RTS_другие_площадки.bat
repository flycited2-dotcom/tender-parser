@echo off
setlocal
cd /d "%~dp0"

set "RTS_BROWSER_EXE=C:\RTSBrowser\rts-chromium.exe"
set "RTS_PROFILE=%LOCALAPPDATA%\RTSCollectorProfile"
if not exist "%RTS_BROWSER_EXE%" (
    echo Dedicated RTS browser not found: %RTS_BROWSER_EXE%
    pause
    exit /b 1
)

if not exist "%RTS_PROFILE%" mkdir "%RTS_PROFILE%"

start "RTS Collector Browser" "%RTS_BROWSER_EXE%" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%RTS_PROFILE%" --no-first-run --no-default-browser-check "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx#tradeSearchTitle" "https://www.rts-tender.ru/poisk/search?id=7a2edb26-ab8d-4fee-86b4-56514059add7"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -r requirements.txt
)

call ".venv\Scripts\activate.bat"
python -m tender_parser rts-watch
pause
