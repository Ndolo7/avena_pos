@echo off
REM Browser mode. Add --host 0.0.0.0 to open it from phones/tablets/PCs on the same network.
cd /d "%~dp0"
py -m pip install pyserial cryptography segno
py app.py --browser
pause
