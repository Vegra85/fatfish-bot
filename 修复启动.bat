@echo off
title QQBot Setup
cd /d C:\QQBot

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Need administrator rights.
    echo  Right-click this file - "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo [1/3] Cleaning leftover processes...
taskkill /f /t /im QQ.exe >nul 2>&1
rem only stop the bot and its keep-alive loop - other python windows (like sticker review) keep running
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*botloop.bat*') -or ($_.Name -like 'py*' -and $_.CommandLine -like '*bot.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
ping -n 7 127.0.0.1 >nul

echo [2/3] Creating scheduled task...
schtasks /delete /tn "QQBot_Service" /f >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u = $env:USERDOMAIN + '\' + $env:USERNAME; $a = New-ScheduledTaskAction -Execute 'C:\QQBot\run.bat' -WorkingDirectory 'C:\QQBot'; $t = New-ScheduledTaskTrigger -AtLogOn -User $u; $t.Delay = 'PT1M'; $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; $p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; Register-ScheduledTask -TaskName 'QQBot_Service' -Action $a -Trigger $t -Settings $s -Principal $p -Force -ErrorAction Stop | Out-Null"
if errorlevel 1 (
    echo  PowerShell could not create the task - trying schtasks instead
    schtasks /create /tn "QQBot_Service" /tr "C:\QQBot\run.bat" /sc onlogon /delay 0001:00 /rl highest /f
    if errorlevel 1 (
        echo  [ERROR] Failed to create task
        pause
        exit /b 1
    )
)
echo      OK

echo [3/3] Starting service...
schtasks /run /tn "QQBot_Service"
ping -n 6 127.0.0.1 >nul

echo.
echo  ============================================
echo   DONE
echo   Start : C:\QQBot\start.bat
echo   Stop  : C:\QQBot\stop.bat
echo   It also starts by itself 1 minute after you log in to Windows
echo  ============================================
echo.
ping -n 16 127.0.0.1 >nul
exit /b 0
