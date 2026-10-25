@echo off
rem Restart ONLY the bot (QQ / NapCat stay logged in). Needs admin because the bot runs as admin.
net session >nul 2>&1
if not errorlevel 1 goto :ok
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:ok
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*botloop.bat*') -or ($_.Name -like 'py*' -and $_.CommandLine -like '*bot.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
ping -n 3 127.0.0.1 >nul
cd /d C:\QQBot\bot
if exist data\stop.flag del /q data\stop.flag
start "FatFish" /min cmd /c C:\QQBot\bot\botloop.bat
echo FatFish restarted - QQ was not touched
ping -n 4 127.0.0.1 >nul
