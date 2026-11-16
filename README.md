# Avena Smart Weighing POS v3

One Python app for small-scale traders (kg POS, receipts) and large-scale weighbridges (tonnes, tickets).
Runs as a desktop window or in a browser. Data: %APPDATA%\AvenaPOS\avena_pos.db

## First run on a customer PC
1. Double-click `run.bat` (desktop window) or `run_browser.bat` (browser, http://127.0.0.1:8765).
2. The app shows a **Computer ID**. Send it to the vendor; the vendor generates a licence key (vendor tools) and you paste it in. Copying this folder/exe to another PC does NOT work: the key only opens on the PC it was issued for.
3. First-time setup: shop name, business type (Small trader / Weighbridge / Both - limited by the licence), administrator account.

## Roles
- Operator: weigh & sell / capture weighbridge weights.
- Manager: + products, barcodes, stock, reports, voids, and operators.
- Administrator (installer/vendor): everything, incl. scale connection, business type, decimals/units, licence.

## Settings (Administrator)
- Decimal places (0-3) and show weights in kg or tonnes.
- How the weight is taken: Automatic (waits until stable) or Scale PRINT button.
- Stable tolerance: ~0.002 kg for shop scales, 5-20 kg for truck scales.
- Weighbridge: ticket prefix, indicator model, first-weighing default, logo.

## Weighbridge flow
Enter vehicle -> Capture first weight (gross or tare) -> later "Capture second weight" -> ticket with net weight.
Vehicles remember their tare, so repeat trucks can be done with a single weighing.

## Barcodes
Products > Barcode field (click it and scan). On Sell, scan any time: weighed items use the scale weight, fixed items add 1.
Demo without a scale: it starts a demo scale automatically when no COM port is found.
