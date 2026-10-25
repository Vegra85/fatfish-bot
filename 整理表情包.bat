@echo off
title FatFish - sticker review (step 1)
cd /d C:\QQBot\bot
echo Installing Pillow (image library) if missing...
python -m pip install pillow -q
echo.
echo Scanning the sticker pack with local AI. This can take 1-2 hours.
echo You can close this window any time - next run continues where it stopped.
echo.
python sticker_curate.py
echo.
if exist C:\QQBot\sticker_review\running.lock (
  echo [!] NOT finished - it was stopped halfway. Double-click this file again to continue.
) else (
  echo ALL DONE. Results are in C:\QQBot\sticker_review
)
pause
