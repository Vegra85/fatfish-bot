@echo off
rem FatFish menu: start / stop / restart / control panel / auto-start / install / maintenance.
rem All the logic is in tools\fish.py - if Windows blocks this file, double-click tools\fish.py instead.
title FatFish
cd /d "%~dp0"
python tools\fish.py %*
if not errorlevel 9009 exit /b %errorlevel%
py -3 tools\fish.py %*
if not errorlevel 9009 exit /b %errorlevel%
echo.
echo  [ERROR] Python was not found. Install Python 3.12 or newer
echo  (on its first screen tick "Add python.exe to PATH"), then double-click this file again.
echo.
pause
exit /b 1
