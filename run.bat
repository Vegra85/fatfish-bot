@echo off
rem your bot's QQ number (the account NapCat logs in with)
set BOT_QQ=123456789
echo ===== %DATE% %TIME% START ===== >> C:\QQBot\run.log

taskkill /f /t /im QQ.exe >> C:\QQBot\run.log 2>&1
rem only stop the bot and its keep-alive loop - other python windows (like sticker review) keep running
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*botloop.bat*') -or ($_.Name -like 'py*' -and $_.CommandLine -like '*bot.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >> C:\QQBot\run.log 2>&1
ping -n 7 127.0.0.1 >nul

cd /d C:\QQBot\bot
if exist data\stop.flag del /q data\stop.flag
start "FatFish" /min cmd /c C:\QQBot\bot\botloop.bat

cd /d C:\QQBot\NapCat
call launcher.bat %BOT_QQ% >> C:\QQBot\napcat.log 2>&1
