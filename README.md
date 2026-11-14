# Avena Smart Weighing POS v2 (no login, no PINs)

Python app: reads the RS-232 scale, locks stable weights, bills, saves to SQLite (%APPDATA%\AvenaPOS\pos.db).
Runs as a desktop window or in a browser.

## Run (Windows)
- Double-click run.bat (desktop window) or run_browser.bat (browser, http://127.0.0.1:8765)
- Manual: py -m pip install -r requirements.txt  then  py app.py  (or py app.py --browser)
- Phones/tablets on the same Wi-Fi: py app.py --browser --host 0.0.0.0 and open the address it prints.
- WSL/Linux: pip install pyserial, then python app.py --browser (open http://127.0.0.1:8765 in Windows).

## Demo without a scale
With no scale connected it starts a demo scale: on Sell tap 0.5 / 1.45 / 2.3 / 5 kg, watch it settle, tap a product.

## Real scale
Scale & Settings -> COM port + baud -> format -> Save & connect. Watch "Live data from scale" and adjust the format (custom regex) until the weight parses.

## .exe
build_exe.bat -> dist\AvenaSmartPOS.exe
