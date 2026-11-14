@echo off
REM Browser mode. Add --host 0.0.0.0 to open it from phones/tablets on the same Wi-Fi.
cd /d "%~dp0"
py -m pip install pyserial
py app.py --browser
pause
