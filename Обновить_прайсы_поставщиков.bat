@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m tender_parser supplier-index --base-dir "%CD%"
) else (
  python -m tender_parser supplier-index --base-dir "%CD%"
)
pause
