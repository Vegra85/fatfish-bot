@echo off
title FatFish - voice listening setup (SenseVoice)
cd /d C:\QQBot\bot
echo Installing local speech recognition (sherpa-onnx + SenseVoice model, about 250 MB).
echo This only needs to run once. Results are saved to C:\QQBot\voice_test.txt
echo.
python setup_voice.py
echo.
echo Now restart the bot: run stop .bat, then start .bat
pause
