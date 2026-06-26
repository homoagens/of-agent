@echo off
REM Install OF-Agent: Python venv + dependencies.
cd /d "%~dp0"
if not exist venv ( echo Creating virtual environment... & python -m venv venv )
echo Installing Python dependencies...
call venv\Scripts\pip install -r requirements.txt
echo.
echo Done. Run configure.bat then start.bat
