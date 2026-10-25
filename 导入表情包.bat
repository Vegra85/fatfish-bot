@echo off
title FatFish - sticker import
cd /d C:\QQBot\bot
echo Installing Pillow (image library) if missing...
python -m pip install pillow -q
echo.
echo Importing stickers:
echo  - about 320 animated stickers, downloaded from the artist site kiriyuki.com
echo  - 231 picked from the DeepSeek sticker pack on your Desktop
echo This takes a few minutes. Safe to run again - it only fetches what is missing.
echo.
python import_nekowhale.py
echo.
echo Report saved to C:\QQBot\sticker_import.txt
echo Now restart the bot: run stop .bat, then start .bat
pause
