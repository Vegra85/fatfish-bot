@echo off
schtasks /run /tn "QQBot_Service"
echo FatFish starting... wait 30 seconds
ping -n 6 127.0.0.1 >nul
