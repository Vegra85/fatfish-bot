@echo off
title FatFish first-time setup
cd /d "%~dp0"
echo.
echo  ===== FatFish first-time setup =====
echo.
python --version >nul 2>&1
if errorlevel 1 goto nopython
echo  Installing the libraries the bot needs (China mirror, about a minute) ...
echo.
python -m pip install --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto pipfail
echo.
python tools\setup_wizard.py
echo.
pause
exit /b 0

:nopython
echo  [ERROR] Python was not found.
echo  Install python-3.13-amd64.exe from the group files.
echo  On its first screen tick "Add python.exe to PATH", then run this file again.
echo.
pause
exit /b 1

:pipfail
echo.
echo  [ERROR] Installing the libraries failed. Check the network, then run this file again.
echo.
pause
exit /b 1
