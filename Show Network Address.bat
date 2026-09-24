@echo off
echo.
echo ==============================================
echo   Connect Four - Network Address Finder
echo ==============================================
echo.
echo Other computers on the SAME WiFi/network should
echo open their browser and go to one of the addresses
echo below, followed by :5000
echo.
echo Example: if it says 192.168.1.42, they should type
echo          http://192.168.1.42:5000
echo.
echo ----------------------------------------------
ipconfig | findstr /i "IPv4"
echo ----------------------------------------------
echo.
echo This PC itself can still just use:
echo          http://127.0.0.1:5000
echo.
pause