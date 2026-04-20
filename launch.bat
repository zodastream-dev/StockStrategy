@echo off
chcp 65001 >nul
cd /d "C:\Users\陆峻\WorkBuddy\20260419220645"
set PYTHONIOENCODING=utf-8
echo Starting server on http://127.0.0.1:5050 ...
"C:\Users\陆峻\.workbuddy\binaries\python\versions\3.13.12\python.exe" -X utf8 -m strategy_platform.app
