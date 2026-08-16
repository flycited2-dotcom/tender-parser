@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p TENDER_CASE_ID=Введите номер закупки или короткий код дела:
if "%TENDER_CASE_ID%"=="" (
  echo Номер дела не указан.
  pause
  exit /b 1
)
set /p TENDER_CASE_TITLE=Введите название закупки:
python -m tender_parser case-init --case-id "%TENDER_CASE_ID%" --title "%TENDER_CASE_TITLE%"
pause
