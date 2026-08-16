@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m tender_parser control-center --open-browser
