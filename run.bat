@echo off
rem MediAI - development server (sets up the virtual environment on first run)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.13 -m venv .venv || python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" app.py
goto :eof

:error
echo Setup failed. Make sure Python 3.13 is installed.
pause
