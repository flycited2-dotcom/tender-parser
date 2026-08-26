@echo off
setlocal

set "YANDEX_BROWSER_EXE=C:\Program Files\Yandex\YandexBrowser\Application\browser.exe"
if not exist "%YANDEX_BROWSER_EXE%" set "YANDEX_BROWSER_EXE=%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"

if not exist "%YANDEX_BROWSER_EXE%" (
    echo Yandex Browser not found. Open GPB manually: https://etp.gpb.ru/#log/maillist/223
    exit /b 1
)

start "ETP GPB - Yandex Browser" "%YANDEX_BROWSER_EXE%" "https://etp.gpb.ru/#log/maillist/223"
exit /b 0
