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

start "Tender Collector Browser" "%RTS_BROWSER_EXE%" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%RTS_PROFILE%" --no-first-run --no-default-browser-check "https://223.rts-tender.ru/supplier/auction/Trade/Search.aspx#tradeSearchTitle" "https://app.rts-tender.ru/supplier/auction/Trade/Search.aspx" "https://market.rts-tender.ru/search/sell?s=1&regs=9100000000000&regs=9200000000000&regs=9000000000000&regs=9500000000000&sts=0&sts=11&smp=0&isHomeReg=false&tst=0&sort=-PublicationDate" "https://www.rts-tender.ru/poisk/search?id=7a2edb26-ab8d-4fee-86b4-56514059add7" "https://agregatoreat.ru/lk/supplier/eat/purchases/active/all" "https://lk.roseltorg.ru/" "https://44.sberbank-ast.ru/tradezone/Supplier/ESPurchaseList.aspx" "https://utp.sberbank-ast.ru/Trade/List/BidListClose"

call "%~dp0open_gpb_yandex.bat"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install -r requirements.txt
)

call ".venv\Scripts\activate.bat"
python -m tender_parser rts-watch
pause
