@echo off
REM Launch the OF-Agent web UI (port 7862) and open the browser.
cd /d "%~dp0"
start "" http://localhost:7862
venv\Scripts\python app.py
