@echo off
rem Make QQ + the bot start by themselves every time you log in to Windows.
rem Also removes the old task's "stop after 3 days" limit (Windows default for schtasks tasks).
rem Run once: right-click this file - "Run as administrator".
title FatFish auto-start
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Need administrator rights.
    echo  Right-click this file - "Run as administrator"
    echo.
    pause
    exit /b 1
)
echo Creating the QQBot_Service task (starts 1 minute after you log in, no time limit)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u = $env:USERDOMAIN + '\' + $env:USERNAME; $a = New-ScheduledTaskAction -Execute 'C:\QQBot\run.bat' -WorkingDirectory 'C:\QQBot'; $t = New-ScheduledTaskTrigger -AtLogOn -User $u; $t.Delay = 'PT1M'; $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; $p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; Register-ScheduledTask -TaskName 'QQBot_Service' -Action $a -Trigger $t -Settings $s -Principal $p -Force -ErrorAction Stop | Out-Null"
if errorlevel 1 (
    echo  PowerShell could not create the task - trying schtasks instead
    schtasks /create /tn "QQBot_Service" /tr "C:\QQBot\run.bat" /sc onlogon /delay 0001:00 /rl highest /f
    if errorlevel 1 (
        echo  [ERROR] Failed to create the task
        pause
        exit /b 1
    )
)
echo.
echo  OK - from now on, 1 minute after you log in to Windows, QQ and the bot start by themselves.
echo  When the bot starts it catches up on missed messages and answers every private chat it has not answered.
echo  Windows will no longer stop them after 3 days.
echo  Note: at that moment any running QQ.exe is closed first (the bot account runs inside QQ).
echo.
choice /c YN /m "Restart QQ + bot now so this applies right away (QQ logs in once more)"
if errorlevel 2 goto later
schtasks /end /tn "QQBot_Service" >nul 2>&1
schtasks /run /tn "QQBot_Service"
echo  Restarting - give it about 30 seconds.
goto done
:later
echo  OK - it applies from the next time you log in. If QQ/bot stop before then, double-click the start script.
:done
echo.
pause
