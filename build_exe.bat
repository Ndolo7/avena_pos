@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --onefile --windowed --name AvenaSmartPOS --add-data "index.html;." app.py
echo Done: dist\AvenaSmartPOS.exe
