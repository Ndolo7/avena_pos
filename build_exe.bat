@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --onefile --windowed --name AvenaSmartPOS --add-data "index.html;." --hidden-import win32print app.py
echo Done: dist\AvenaSmartPOS.exe
