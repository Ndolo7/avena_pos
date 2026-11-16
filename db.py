"""SQLite data layer: users, products, sales ledger, stock, audit log."""
import sqlite3, os, hashlib, secrets, datetime as dt


def data_dir():
    d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AvenaPOS")
    os.makedirs(d, exist_ok=True)
    return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw TEXT, role TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, plu TEXT UNIQUE, name TEXT, unit_type TEXT, price REAL, active INTEGER DEFAULT 1, barcode TEXT);
CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, ts TEXT, user_id INTEGER, total REAL, cash REAL, mpesa REAL, credit REAL,
  mpesa_ref TEXT, customer TEXT, tendered REAL, change_given REAL, voided INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY, sale_id INTEGER, product_id INTEGER, name TEXT, unit_type TEXT,
  qty REAL, unit_price REAL, line_total REAL, manual INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock(product_id INTEGER, day TEXT, opening REAL DEFAULT 0, received REAL DEFAULT 0, PRIMARY KEY(product_id, day));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, ticket_no TEXT, created TEXT, customer TEXT, driver TEXT, vehicle TEXT, trailer TEXT,
  product TEXT, destination TEXT, remarks TEXT, first_type TEXT, first_kg REAL, first_ts TEXT, second_kg REAL, second_ts TEXT, gross_kg REAL, tare_kg REAL,
  net_kg REAL, wmode TEXT, operator TEXT, indicator TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS vehicles(vehicle TEXT PRIMARY KEY, tare_kg REAL, updated TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT, detail TEXT);
"""
DEFAULTS = {"shop_name": "", "shop_address": "", "printer": "", "receipt_width": "32", "auto_print": "0",
            "port": "", "baud": "9600", "profile": "Generic (ST,GS,+001.250kg)", "mode": "continuous", "custom_regex": "", "reverse": "0",
            "biz_mode": "retail", "decimals": "3", "display_unit": "kg", "scale_unit": "kg", "stable_tol": "0.002", "allow_manual": "0",
            "first_weigh": "gross", "ticket_prefix": "WB-", "indicator": "", "logo": "", "last_seen": "", "ticket_secret": ""}



def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DB:
    def __init__(self, path=None):
        self.c = sqlite3.connect(path or os.path.join(data_dir(), "pos.db"), check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        if "barcode" not in [r[1] for r in self.q("PRAGMA table_info(products)")]:
            self.c.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
        self.c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_barcode ON products(barcode) WHERE barcode IS NOT NULL")
        for k, v in DEFAULTS.items():
            self.c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
        self.c.commit()

    def q(self, sql, a=()):
        return self.c.execute(sql, a).fetchall()

    def x(self, sql, a=()):
        cur = self.c.execute(sql, a); self.c.commit(); return cur.lastrowid

    def get(self, k):
        r = self.q("SELECT value FROM settings WHERE key=?", (k,))
        return r[0][0] if r else DEFAULTS.get(k, "")

    def put(self, k, v):
        self.x("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))

    def audit(self, user, action, detail=""):
        self.x("INSERT INTO audit(ts,user,action,detail) VALUES(?,?,?,?)", (now(), user, action, detail))

    # users
    def add_user(self, u, pw, role):
        self.x("INSERT INTO users(username,pw,role) VALUES(?,?,?)", (u, hash_pw(pw), role))

    def login(self, u, pw):
        r = self.q("SELECT * FROM users WHERE username=? AND active=1", (u,))
        if r and secrets.compare_digest(hash_pw(pw, r[0]["pw"].split("$")[0]), r[0]["pw"]):
            return dict(r[0])

    def set_password(self, u, pw):
        self.x("UPDATE users SET pw=? WHERE username=?", (hash_pw(pw), u))

    def users(self):
        return self.q("SELECT id,username,role FROM users WHERE active=1 ORDER BY username")

    def deactivate_user(self, uid):
        self.x("UPDATE users SET active=0 WHERE id=?", (uid,))

    # products
    def products(self):
        return self.q("SELECT * FROM products WHERE active=1 ORDER BY CAST(plu AS INTEGER), name")

    def save_product(self, pid, plu, name, unit_type, price, barcode=None):
        barcode = (barcode or "").strip() or None
        if pid:
            self.x("UPDATE products SET plu=?,name=?,unit_type=?,price=?,barcode=? WHERE id=?", (plu, name, unit_type, price, barcode, pid))
        else:
            self.x("INSERT INTO products(plu,name,unit_type,price,barcode) VALUES(?,?,?,?,?)", (plu, name, unit_type, price, barcode))

    def delete_product(self, pid):
        self.x("UPDATE products SET active=0, barcode=NULL, plu=plu||'~'||id WHERE id=?", (pid,))

    # sales
    def create_sale(self, user_id, items, pay):
        lines = [round(i["qty"] * i["unit_price"], 2) for i in items]
        total = round(sum(lines), 2)
        cash, mp, cr = (round(pay.get(k, 0), 2) for k in ("cash", "mpesa", "credit"))
        if abs(cash + mp + cr - total) > 0.01:
            raise ValueError("Payment parts do not equal the bill total")
        sid = self.x("INSERT INTO sales(ts,user_id,total,cash,mpesa,credit,mpesa_ref,customer,tendered,change_given) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (now(), user_id, total, cash, mp, cr, pay.get("ref", ""), pay.get("customer", ""),
                      pay.get("tendered", cash), pay.get("change", 0)))
        for i, lt in zip(items, lines):
            self.c.execute("INSERT INTO sale_items(sale_id,product_id,name,unit_type,qty,unit_price,line_total,manual) VALUES(?,?,?,?,?,?,?,?)",
                           (sid, i["product_id"], i["name"], i["unit_type"], i["qty"], i["unit_price"], lt, int(i.get("manual", 0))))
        self.c.commit()
        return sid

    def sale(self, sid):
        s = self.q("SELECT s.*, u.username FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,))[0]
        return dict(s), [dict(r) for r in self.q("SELECT * FROM sale_items WHERE sale_id=?", (sid,))]

    def void_sale(self, sid, user):
        self.x("UPDATE sales SET voided=1 WHERE id=?", (sid,))
        self.audit(user, "void_sale", str(sid))

    def sales(self, d1, d2):
        return self.q("SELECT s.*, u.username FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE date(ts) BETWEEN ? AND ? ORDER BY s.id DESC", (d1, d2))

    def z_report(self, d1, d2):
        t = self.q("SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(cash),0) cash, COALESCE(SUM(mpesa),0) mpesa, COALESCE(SUM(credit),0) credit "
                   "FROM sales WHERE voided=0 AND date(ts) BETWEEN ? AND ?", (d1, d2))[0]
        prod = self.q("SELECT i.name, i.unit_type, SUM(i.qty) qty, SUM(i.line_total) revenue FROM sale_items i JOIN sales s ON s.id=i.sale_id "
                      "WHERE s.voided=0 AND date(s.ts) BETWEEN ? AND ? GROUP BY i.name, i.unit_type ORDER BY revenue DESC", (d1, d2))
        att = self.q("SELECT u.username, COUNT(*) n, SUM(s.total) total FROM sales s LEFT JOIN users u ON u.id=s.user_id "
                     "WHERE s.voided=0 AND date(s.ts) BETWEEN ? AND ? GROUP BY u.username", (d1, d2))
        return {"from": d1, "to": d2, "totals": dict(t), "products": [dict(r) for r in prod], "attendants": [dict(r) for r in att]}

    # stock (kg products)
    def stock_set(self, pid, day, opening, received):
        self.x("INSERT OR REPLACE INTO stock VALUES(?,?,?,?)", (pid, day, opening, received))

    def stock_rows(self, day):
        out = []
        for p in self.q("SELECT * FROM products WHERE active=1 AND unit_type='kg' ORDER BY name"):
            st = self.q("SELECT opening,received FROM stock WHERE product_id=? AND day=?", (p["id"], day))
            op, rc = (st[0]["opening"], st[0]["received"]) if st else (0.0, 0.0)
            sold = self.q("SELECT COALESCE(SUM(i.qty),0) FROM sale_items i JOIN sales s ON s.id=i.sale_id "
                          "WHERE i.product_id=? AND s.voided=0 AND date(s.ts)=?", (p["id"], day))[0][0]
            out.append({"id": p["id"], "name": p["name"], "opening": op, "received": rc, "sold": round(sold, 3),
                        "expected": round(op + rc - sold, 3)})
        return out

    # ---- users (extra) ----
    def has_users(self):
        return bool(self.q("SELECT 1 FROM users WHERE active=1"))

    def user_row(self, uid):
        r = self.q("SELECT id,username,role FROM users WHERE id=? AND active=1", (uid,))
        return dict(r[0]) if r else None

    def set_role(self, username, role):
        self.x("UPDATE users SET role=? WHERE username=?", (role, username))

    def admin_count(self):
        return self.q("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1")[0][0]

    # ---- weighbridge tickets ----
    @staticmethod
    def _calc(first_type, first, second):
        gross, tare = (first, second) if first_type == "gross" else (second, first)
        if gross <= tare:
            raise ValueError(f"Gross ({gross:,.0f} kg) must be heavier than tare ({tare:,.0f} kg). Check which weighing came first.")
        return gross, tare, round(gross - tare, 3)

    @staticmethod
    def vkey(v):
        return "".join((v or "").upper().split())

    def vehicle_tare(self, vehicle):
        r = self.q("SELECT tare_kg, updated FROM vehicles WHERE vehicle=?", (self.vkey(vehicle),))
        return r[0]["tare_kg"] if r else None

    def vehicle_info(self, vehicle):
        r = self.q("SELECT tare_kg, updated FROM vehicles WHERE vehicle=?", (self.vkey(vehicle),))
        return dict(r[0]) if r else None

    def create_ticket(self, f, first_type, first_kg, operator, indicator, prefix, stored_tare=None):
        if stored_tare is not None:
            self._calc("gross", first_kg, stored_tare)  # validate before saving anything
        ts = now()
        tid = self.x("INSERT INTO tickets(created,customer,driver,vehicle,trailer,product,destination,remarks,first_type,first_kg,first_ts,operator,indicator,status) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open')",
                     (ts, f.get("customer", ""), f.get("driver", ""), f["vehicle"], f.get("trailer", ""), f.get("product", ""),
                      f.get("destination", ""), f.get("remarks", ""), first_type, first_kg, ts, operator, indicator))
        self.x("UPDATE tickets SET ticket_no=? WHERE id=?", (f"{prefix}{tid:06d}", tid))
        if stored_tare is not None:
            self._finish(tid, stored_tare, ts, "Single Weighing (stored tare)")
        return tid

    def close_ticket(self, tid, second_kg):
        t = self.q("SELECT status FROM tickets WHERE id=?", (tid,))
        if not t or t[0]["status"] != "open":
            raise ValueError("This ticket is not waiting for a second weighing")
        self._finish(tid, second_kg, now(), "Double Weighing")

    def _finish(self, tid, second_kg, ts, wmode):
        t = dict(self.q("SELECT * FROM tickets WHERE id=?", (tid,))[0])
        gross, tare, net = self._calc(t["first_type"], t["first_kg"], second_kg)
        self.x("UPDATE tickets SET second_kg=?,second_ts=?,gross_kg=?,tare_kg=?,net_kg=?,wmode=?,status='done' WHERE id=?",
               (second_kg, ts, gross, tare, net, wmode, tid))
        if wmode.startswith("Double"):
            self.x("INSERT OR REPLACE INTO vehicles VALUES(?,?,?)", (self.vkey(t["vehicle"]), tare, ts[:10]))

    def ticket(self, tid):
        r = self.q("SELECT * FROM tickets WHERE id=?", (tid,))
        return dict(r[0]) if r else None

    def open_tickets(self):
        return [dict(r) for r in self.q("SELECT * FROM tickets WHERE status='open' ORDER BY id")]

    def tickets(self, d1, d2):
        return [dict(r) for r in self.q("SELECT * FROM tickets WHERE date(first_ts) BETWEEN ? AND ? ORDER BY id DESC", (d1, d2))]

    def void_ticket(self, tid, user):
        self.x("UPDATE tickets SET status='void' WHERE id=?", (tid,)); self.audit(user, "void_ticket", str(tid))

    def ticket_report(self, d1, d2):
        t = self.q("SELECT COUNT(*) n, COALESCE(SUM(net_kg),0) net FROM tickets WHERE status='done' AND date(first_ts) BETWEEN ? AND ?", (d1, d2))[0]
        bp = self.q("SELECT COALESCE(NULLIF(product,''),'(none)') product, COUNT(*) n, SUM(net_kg) net FROM tickets WHERE status='done' AND date(first_ts) BETWEEN ? AND ? GROUP BY 1 ORDER BY net DESC", (d1, d2))
        return {"totals": dict(t), "by_product": [dict(r) for r in bp], "tickets": self.tickets(d1, d2)}
