@echo off
rem MediAI - production server (gevent). Configure via .env first (see .env.example).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment missing - run run.bat once first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" serve.py
