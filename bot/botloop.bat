@echo off
rem FatFish keep-alive loop: runs bot.py and starts it again if it stops or crashes.
rem   exit code 4 = restart requested (start again right away)
rem   exit code 3 = another bot is already running (stop this loop)
rem   data\stop.flag = stopped on purpose from the control panel (stop this loop)
rem To stop everything use the stop script in C:\QQBot.
title FatFish
cd /d C:\QQBot\bot
if exist data\stop.flag del /q data\stop.flag
set FATFISH_LOOP=1
:loop
python bot.py >> C:\QQBot\bot\bot.log 2>&1
set RC=%errorlevel%
if exist data\stop.flag goto end
if "%RC%"=="3" goto end
if "%RC%"=="4" goto loop
echo [loop] bot.py stopped (exit code %RC%) - starting it again in 15 seconds >> C:\QQBot\bot\bot.log
ping -n 16 127.0.0.1 >nul
if exist data\stop.flag goto end
goto loop
:end
if exist data\stop.flag del /q data\stop.flag
