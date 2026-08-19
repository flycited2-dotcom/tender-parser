@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p SUPPLIER_QUERY=Введите товар, модель или артикул: 
if "%SUPPLIER_QUERY%"=="" exit /b 2
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m tender_parser supplier-search --base-dir "%CD%" --query "%SUPPLIER_QUERY%" --limit 15
) else (
  python -m tender_parser supplier-search --base-dir "%CD%" --query "%SUPPLIER_QUERY%" --limit 15
)
pause
