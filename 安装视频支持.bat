@echo off
title FatFish - video support (ffmpeg)
where ffmpeg >nul 2>&1
if not errorlevel 1 (
    echo ffmpeg already installed:
    where ffmpeg
    goto done
)
echo Installing ffmpeg with winget (official package Gyan.FFmpeg)...
echo If winget asks you to accept its terms, type Y and press Enter.
winget install --id Gyan.FFmpeg -e
:done
echo.
echo Now restart the bot: run stop .bat, then start .bat
echo bot.log should show a line with ffmpeg OK after restart.
pause
