@echo off
cd /d "%~dp0"

if not exist venv (
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

start "" http://127.0.0.1:5678/
python server.py
