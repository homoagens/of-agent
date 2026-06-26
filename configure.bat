@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ========================================
echo   OF-Agent - LLM configuration
echo ========================================
echo.
echo This will write a .env file for OF-Agent.
echo Point it at any OpenAI-compatible endpoint
echo (Ollama, LM Studio, vLLM, llama.cpp, OpenAI, Groq, OpenRouter...).
echo.

set "DEFAULT_URL=http://localhost:11434"
set "DEFAULT_MODEL=llama3.1:8b"

set /p BASE_URL=Backend URL [%DEFAULT_URL%]:
if "!BASE_URL!"=="" set "BASE_URL=%DEFAULT_URL%"

set /p MODEL=Model name [%DEFAULT_MODEL%]:
if "!MODEL!"=="" set "MODEL=%DEFAULT_MODEL%"

set /p API_KEY=API key [local]:
if "!API_KEY!"=="" set "API_KEY=local"

set /p TEMP=Temperature [0.2]:
if "!TEMP!"=="" set "TEMP=0.2"

if exist .env (
    copy /Y .env .env.backup >nul
    echo.
    echo Existing .env backed up to .env.backup
)

(
    echo OF_AGENT_BACKEND_URL=!BASE_URL!
    echo OF_AGENT_BACKEND_KEY=!API_KEY!
    echo OF_AGENT_MODEL=!MODEL!
    echo OF_AGENT_TEMPERATURE=!TEMP!
) > .env

echo.
echo Configuration saved to .env:
echo   backend URL:  !BASE_URL!
echo   model:        !MODEL!
echo   temperature:  !TEMP!
echo.
endlocal
