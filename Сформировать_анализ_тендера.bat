@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p TENDER_CASE_ID=Введите номер закупки/код дела или нажмите Enter для последнего дела:
if "%TENDER_CASE_ID%"=="" (
  for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "$p=Get-ChildItem -LiteralPath '.\cases' -Directory ^| Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'case.json') } ^| Sort-Object { (Get-Item -LiteralPath (Join-Path $_.FullName 'case.json')).LastWriteTime } -Descending ^| Select-Object -First 1 -ExpandProperty Name; $p"`) do set "TENDER_CASE_ID=%%D"
)
if "%TENDER_CASE_ID%"=="" (
  echo Тендерные дела не найдены. Сначала создайте дело.
  pause
  exit /b 1
)
python -m tender_parser case-preflight --case-id "%TENDER_CASE_ID%"
if errorlevel 1 (
  echo Предварительный анализ завершился с ошибкой.
  pause
  exit /b 1
)
echo.
echo Предварительный анализ готов. Открываю папку результата.
start "" "%~dp0cases\%TENDER_CASE_ID%\output"
pause
