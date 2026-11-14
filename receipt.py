"""ESC/POS thermal receipts (58mm=32 cols, 80mm=48 cols) + Z-report text."""
ESC_INIT, CENTER, LEFT, BOLD_ON, BOLD_OFF, CUT = b"\x1b@", b"\x1ba\x01", b"\x1ba\x00", b"\x1bE\x01", b"\x1bE\x00", b"\x1dVB\x00"


def _row(l, r, w):
    return l[:max(0, w - len(r) - 1)].ljust(w - len(r)) + r


def receipt_text(shop, sale, items, w=32):
    L = [shop["name"].center(w)]
    if shop.get("address"):
        L.append(shop["address"].center(w))
    L += ["-" * w, f"Receipt #{sale['id']}", f"{sale['ts']}  {sale.get('username') or ''}", "-" * w]
    for i in items:
        L.append(i["name"][:w])
        q = f"{i['qty']:.3f} kg" if i["unit_type"] == "kg" else f"{i['qty']:g} x"
        L.append(_row(f"  {q} @ {i['unit_price']:,.2f}", f"{i['line_total']:,.2f}", w))
    L += ["-" * w, _row("TOTAL (KES)", f"{sale['total']:,.2f}", w)]
    if sale["cash"]:
        L.append(_row("Cash", f"{sale['cash']:,.2f}", w))
    if sale["mpesa"]:
        L.append(_row("M-Pesa " + (sale["mpesa_ref"] or ""), f"{sale['mpesa']:,.2f}", w))
    if sale["credit"]:
        L.append(_row("Credit " + (sale["customer"] or ""), f"{sale['credit']:,.2f}", w))
    if sale["change_given"]:
        L.append(_row("Change", f"{sale['change_given']:,.2f}", w))
    L += ["-" * w, "Thank you!".center(w), ""]
    return "\n".join(L)


def receipt_bytes(shop, sale, items, w=32):
    body = receipt_text(shop, sale, items, w).encode("cp437", "replace")
    return ESC_INIT + body + b"\n\n\n" + CUT


def print_raw(printer_name, data):
    """Send raw ESC/POS bytes to a Windows printer (needs pywin32)."""
    import win32print
    h = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(h, 1, ("Receipt", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def z_text(rep, shop="", w=48):
    t = rep["totals"]
    L = [f"{shop} - Z REPORT".center(w), f"{rep['from']} to {rep['to']}".center(w), "=" * w,
         _row("Transactions", str(t["n"]), w), _row("Total revenue", f"{t['total']:,.2f}", w),
         _row("  Cash", f"{t['cash']:,.2f}", w), _row("  M-Pesa", f"{t['mpesa']:,.2f}", w),
         _row("  Credit", f"{t['credit']:,.2f}", w), "-" * w, "SALES BY PRODUCT"]
    for p in rep["products"]:
        q = f"{p['qty']:.3f} kg" if p["unit_type"] == "kg" else f"{p['qty']:g} pcs"
        L.append(_row(f"{p['name']} ({q})", f"{p['revenue']:,.2f}", w))
    L += ["-" * w, "ATTENDANTS"]
    L += [_row(f"{a['username']} ({a['n']} sales)", f"{a['total']:,.2f}", w) for a in rep["attendants"]]
    return "\n".join(L)


def export_csv(db, d1, d2, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig opens cleanly in Excel
        wr = csv.writer(f)
        wr.writerow(["Sale ID", "Date/Time", "Attendant", "Item", "Qty", "Unit", "Unit Price", "Line Total", "Sale Total", "Cash", "M-Pesa", "M-Pesa Ref", "Credit", "Customer", "Voided"])
        for s in reversed(db.sales(d1, d2)):
            for i in db.sale(s["id"])[1]:
                wr.writerow([s["id"], s["ts"], s["username"], i["name"], i["qty"], i["unit_type"], i["unit_price"],
                             i["line_total"], s["total"], s["cash"], s["mpesa"], s["mpesa_ref"], s["credit"], s["customer"], "YES" if s["voided"] else ""])
