import os, tempfile
from scale import parse_frame, PROFILES, ScaleReader
from db import DB
import receipt
g = PROFILES["Generic (ST,GS,+001.250kg)"]["regex"]
assert parse_frame("ST,GS,+001.250kg", g) == (1.25, True)
assert parse_frame("US,NT,+000.850kg", g) == (0.85, False)
assert parse_frame("  1250 g", g)[0] == 1.25
y = PROFILES["Yaohua reversed '=' frame"]
assert parse_frame("=052.100", y["regex"], True)[0] == 1.25
r = ScaleReader()
for _ in range(6): r.feed("1.450 kg") if False else r.feed("ST,GS,+001.450kg")
r.connected = True; r.last_frame = __import__("time").time()
assert r.take() == 1.45
r2 = ScaleReader(); r2.configure("Plain number (1.250 kg)"); r2.connected = True
for _ in range(5): r2.feed("2.000 kg")
assert r2.take() is None; r2.feed("2.000 kg"); assert r2.take() == 2.0
db = DB(os.path.join(tempfile.mkdtemp(), "t.db"))
assert db.login("admin", "admin123") and not db.login("admin", "x")
db.save_product(None, "1", "Beef With Bone", "kg", 600); db.save_product(None, "2", "Oil", "fixed", 250)
p = db.products(); u = db.login("admin", "admin123")
sid = db.create_sale(u["id"], [dict(product_id=p[0]["id"], name="Beef With Bone", unit_type="kg", qty=1.45, unit_price=600),
                               dict(product_id=p[1]["id"], name="Oil", unit_type="fixed", qty=1, unit_price=250)],
                     {"cash": 1120, "tendered": 1200, "change": 80})
s, it = db.sale(sid); assert s["total"] == 1120.0
print(receipt.receipt_text({"name": "Test Butchery", "address": "Machakos"}, s, it, 32))
d = s["ts"][:10]; db.stock_set(p[0]["id"], d, 85, 0)
assert db.stock_rows(d)[0]["expected"] == 83.55
rep = db.z_report(d, d); assert rep["totals"]["total"] == 1120
print(receipt.z_text(rep, "Test", 40)); receipt.export_csv(db, d, d, "/tmp/x.csv")
db.void_sale(sid, "admin"); assert db.z_report(d, d)["totals"]["total"] == 0
print("ALL TESTS PASSED")
