@echo off
net session >nul 2>&1
if not errorlevel 1 goto :ok
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:ok
schtasks /end /tn "QQBot_Service" >nul 2>&1
taskkill /f /t /im QQ.exe >nul 2>&1
rem only stop the bot and its keep-alive loop - other python windows (like sticker review) keep running
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*botloop.bat*') -or ($_.Name -like 'py*' -and $_.CommandLine -like '*bot.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo FatFish stopped
ping -n 4 127.0.0.1 >nul
