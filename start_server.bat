@echo off
title MockupGen Production Server (Waitress WSGI)
cd /d "%~dp0"

echo ========================================================
echo   Starting MockupGen Server with Waitress WSGI (8 Threads)...
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_server.py
) else (
    python run_server.py
)

pause
