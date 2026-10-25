@echo off
title FatFish - local AI setup
set "MODEL=qwen3-vl:8b-instruct-q8_0"

echo.
echo ===== [1/4] Ollama =====
call :find
if defined OLLAMA goto have
echo Ollama not found. Installing with winget (official package Ollama.Ollama)...
echo If winget asks you to accept its terms, type Y and press Enter.
winget install --id Ollama.Ollama -e
call :find
if defined OLLAMA goto have
echo.
echo [!] Auto install failed. Opening the official download page.
echo     Install OllamaSetup.exe, then double-click this file again.
start "" https://ollama.com/download/windows
pause
exit /b 1

:have
echo Found: %OLLAMA%
"%OLLAMA%" --version

echo.
echo ===== [2/4] Start Ollama service =====
"%OLLAMA%" list >nul 2>&1
if not errorlevel 1 goto running
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
) else (
    start "Ollama" /min "%OLLAMA%" serve
)
ping -n 9 127.0.0.1 >nul
:running
echo Service OK

echo.
echo ===== [3/4] Download %MODEL% (about 10 GB) =====
"%OLLAMA%" pull %MODEL%
if errorlevel 1 (
    echo [!] Download failed. Check network and run this file again.
    pause
    exit /b 1
)

echo.
echo ===== [4/4] Test (vision + memory) =====
cd /d C:\QQBot\bot
python test_local_ai.py

echo.
echo ===== DONE =====
echo Result saved to C:\QQBot\local_ai_test.txt
echo Now restart the bot: run stop .bat, then start .bat
echo.
pause
exit /b 0

:find
set "OLLAMA="
for /f "delims=" %%i in ('where ollama 2^>nul') do if not defined OLLAMA set "OLLAMA=%%i"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
exit /b 0
