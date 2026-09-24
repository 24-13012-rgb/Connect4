@echo off
echo Stopping Connect Four server...
taskkill /F /IM python.exe /T >nul 2>&1
echo Done. You can close this window.
timeout /t 3 >nul
