"""Avena Smart Weighing POS v3 - licensed, role-based, retail + weighbridge.
   python app.py            -> desktop window (needs pywebview) or your browser
   python app.py --browser  -> browser only
   python app.py --host 0.0.0.0  -> also open from other devices on the same Wi-Fi/LAN
"""
import argparse, csv, hashlib, hmac, io, json, os, secrets, socket, sys, threading, time, webbrowser
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from db import DB, data_dir
from scale import ScaleReader, PROFILES, list_serial_ports, autodetect_port
import licensing, receipt

try:
    import segno
except ImportError:
    segno = None

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
LICFILE = os.path.join(data_dir(), "license.key")
db, scale, dblock = DB(os.path.join(data_dir(), "avena_pos.db")), ScaleReader(), threading.RLock()
demo, sessions = False, {}
LEVEL = {"operator": 1, "manager": 2, "admin": 3}
CFG_KEYS = ["shop_name", "shop_address", "biz_mode", "decimals", "display_unit", "mode", "scale_unit", "stable_tol", "allow_manual",
            "first_weigh", "ticket_prefix", "indicator", "logo", "receipt_width", "auto_print"]
ADMIN_KEYS = CFG_KEYS[2:11] + ["logo", "port", "baud", "profile", "custom_regex", "reverse"]
MANAGER_KEYS = ["shop_name", "shop_address", "printer", "receipt_width", "auto_print"]
MIN = {"/api/scale": 1, "/api/take": 1, "/api/products": 1, "/api/sale": 1, "/api/receipt": 1, "/api/print": 1, "/api/ticket/open": 1,
       "/api/ticket/close": 1, "/api/tickets/open": 1, "/api/ticket": 1, "/api/tickets": 1, "/api/vehicle": 1, "/api/scale/sim": 1,
       "/api/product": 2, "/api/product/delete": 2, "/api/stock": 2, "/api/void": 2, "/api/report": 2, "/api/export.csv": 2,
       "/api/tickets/report": 2, "/api/tickets.csv": 2, "/api/ticket/void": 2, "/api/users": 2, "/api/user": 2, "/api/user/delete": 2,
       "/api/settings": 2}
PRESET = {"retail": {"decimals": "3", "stable_tol": "0.002"}, "both": {"decimals": "3", "stable_tol": "0.002"},
          "weighbridge": {"decimals": "0", "stable_tol": "10", "display_unit": "kg"}}


class HTTPError(Exception):
    def __init__(self, code, msg, need=""):
        super().__init__(msg); self.code, self.need = code, need


# ---------- licence / config ----------
_lic = {"t": 0, "v": None}


def lic(force=False):
    if force or time.time() - _lic["t"] > 30:
        _lic["v"], _lic["t"] = licensing.status(db.get, db.put, LICFILE), time.time()
    return _lic["v"]


def allowed_biz(modes):
    m = set(modes)
    return [x for x in ("retail", "weighbridge") if x in m] + (["both"] if len(m) > 1 else [])


def config():
    L, c = lic(), {k: db.get(k) for k in CFG_KEYS}
    c.update(decimals=int(db.get("decimals") or 0), company=L.get("company", ""), modes=L.get("modes", []), expires=L.get("expires"))
    c["biz_modes"] = allowed_biz(c["modes"])
    return c


def boot(user):
    L = lic()
    out = {"machine_id": licensing.machine_id(), "licensed": L["ok"], "reason": L.get("reason", "")}
    if L["ok"]:
        out.update(company=L["company"], modes=L["modes"], needs_setup=not db.has_users(), user=user, config=config())
    return out


# ---------- scale ----------
def start_scale():
    global demo
    g = db.get
    scale.configure(g("profile"), g("custom_regex"), g("reverse") == "1", g("mode"), g("stable_tol") or 0.002, g("scale_unit") or "kg")
    port = g("port") or autodetect_port()
    if port and g("demo") != "1":
        scale.start(port, g("baud") or 9600); demo = False
    else:
        scale.configure("Generic (ST,GS,+001.250kg)", mode=g("mode"), tol=g("stable_tol") or 0.002); scale.start_sim(); demo = True


def take_weight():
    if scale.status() in ("disconnected", "no_data"):
        raise ValueError("The scale is not sending data - check the cable")
    w = scale.take()
    if not w:
        raise ValueError("Waiting for the scale: no stable weight yet" if scale.mode != "button" else "Press PRINT on the scale first")
    dec, t = int(db.get("decimals") or 0), db.get("display_unit") == "t"
    f = 10 ** dec
    v = int((w / 1000 if t else w) * f + 0.5) / f  # round half up (shop style), not banker's rounding
    return round(v * 1000 if t else v, 3)


# ---------- helpers ----------
def rows(r):
    return [dict(x) for x in r]


def shop():
    return {"name": db.get("shop_name"), "address": db.get("shop_address")}


def fmtargs():
    return int(db.get("receipt_width") or 32), int(db.get("decimals") or 3), db.get("display_unit") or "kg"


def rcpt(sid):
    s, it = db.sale(int(sid)); w, d, u = fmtargs()
    return receipt.receipt_text(shop(), s, it, w, d, u)


def do_print(sid):
    try:
        s, it = db.sale(int(sid)); w, d, u = fmtargs()
        receipt.print_raw(db.get("printer"), receipt.receipt_bytes(shop(), s, it, w, d, u))
        return {"printed": True}
    except Exception as e:
        return {"printed": False, "print_error": str(e)}


def verify_code(t):
    if not db.get("ticket_secret"):
        db.put("ticket_secret", secrets.token_hex(16))
    return hmac.new(db.get("ticket_secret").encode(), f"{t['ticket_no']}|{t['net_kg']}".encode(), hashlib.sha256).hexdigest()[:8].upper()


def tk(tid):
    t = db.ticket(int(tid))
    if not t:
        raise ValueError("Ticket not found")
    if t["status"] == "done":
        t["verify"] = verify_code(t)
        t["qr"] = ""
        if segno:
            buf = io.BytesIO()
            segno.make(f"{t['ticket_no']}|{t['vehicle']}|{t['net_kg']:g}kg|{t['first_ts'][:10]}|{t['verify']}", error="m").save(buf, kind="svg", xmldecl=False, nl=False, scale=3, border=1)
            t["qr"] = buf.getvalue().decode()
    return t


def csv_bytes(header, data):
    f = io.StringIO(); w = csv.writer(f); w.writerow(header); w.writerows(data)
    return ("\ufeff" + f.getvalue()).encode("utf-8")


def new_session(u):
    tok = secrets.token_urlsafe(24); sessions[tok] = {"id": u["id"], "username": u["username"], "role": u["role"]}
    return tok, {"id": u["id"], "username": u["username"], "role": u["role"]}


def cookie(tok, age=None):
    return f"sid={tok}; HttpOnly; SameSite=Lax; Path=/" + (f"; Max-Age={age}" if age is not None else "")


# ---------- API ----------
def handle(p, q, b, user, tok):
    if p == "/api/boot":
        return boot(user)
    if p == "/api/license/activate":
        licensing.activate(b.get("key", ""), LICFILE); lic(True)
        return boot(user)
    if p == "/api/setup":
        if not lic()["ok"] or db.has_users():
            raise HTTPError(403, "Setup is already done")
        L = lic(); mode = b.get("biz_mode")
        if mode not in allowed_biz(L["modes"]):
            raise ValueError("That business type is not included in your license")
        if not b.get("username", "").strip() or len(b.get("password", "")) < 4:
            raise ValueError("Enter a username and a password of at least 4 characters")
        db.add_user(b["username"].strip(), b["password"], "admin")
        for k, v in {**PRESET[mode], "biz_mode": mode, "shop_name": (b.get("shop_name") or L["company"]).strip()}.items():
            db.put(k, v)
        start_scale()
        t, u = new_session(db.login(b["username"].strip(), b["password"]))
        return (boot(u), "application/json", {"Set-Cookie": cookie(t)})
    if p == "/api/login":
        u = lic()["ok"] and db.login(b.get("username", "").strip(), b.get("password", ""))
        if not u:
            time.sleep(0.5); raise ValueError("Wrong username or password")
        t, us = new_session(u)
        return (boot(us), "application/json", {"Set-Cookie": cookie(t)})
    if p == "/api/logout":
        sessions.pop(tok, None)
        return ({"ok": 1}, "application/json", {"Set-Cookie": cookie("", 0)})
    if not lic()["ok"]:
        raise HTTPError(403, "License required", "license")
    if not user:
        raise HTTPError(401, "Please log in", "login")
    if LEVEL[user["role"]] < MIN.get(p, 3):
        raise HTTPError(403, "Your role is not allowed to do this")
    me = user["role"]

    # --- scale ---
    if p == "/api/scale":
        with scale.lock:
            cap = scale.captured
        return {"weight": scale.weight, "status": scale.status(), "demo": demo, "port": db.get("port"), "error": scale.error,
                "raw": list(scale.raw)[-12:], "captured": cap, "cap_seq": scale.cap_seq}
    if p == "/api/take":
        try:
            return {"weight": take_weight()}
        except ValueError:
            return {"weight": None}
    if p == "/api/ports":
        return {"ports": [{"device": d, "desc": x} for d, x in list_serial_ports()], "profiles": list(PROFILES)}
    if p == "/api/scale/connect":
        for k in ("port", "baud", "profile", "mode", "custom_regex", "reverse", "scale_unit", "stable_tol"):
            if k in b:
                db.put(k, b[k])
        db.put("demo", "0"); start_scale(); return {"ok": 1, "demo": demo}
    if p == "/api/scale/demo":
        db.put("demo", "1"); start_scale(); return {"ok": 1}
    if p == "/api/scale/sim":
        if not demo:
            raise ValueError("The demo scale is not active")
        if "weight" in b:
            scale.sim_weight = float(b["weight"]); scale.sim_settle = time.time() + 1.0
        if b.get("print"):
            scale.sim_print = True
        return {"ok": 1}

    # --- products / sales ---
    if p == "/api/products":
        return rows(db.products())
    if p == "/api/product":
        db.save_product(b.get("id"), str(b["plu"]).strip(), b["name"].strip(), b["unit_type"], float(b.get("price") or 0), b.get("barcode")); return {"ok": 1}
    if p == "/api/product/delete":
        db.delete_product(b["id"]); return {"ok": 1}
    if p == "/api/sale":
        if any(i.get("manual") for i in b["items"]) and db.get("allow_manual") != "1":
            raise ValueError("Typing weights by hand is switched off in Settings")
        sid = db.create_sale(user["id"], b["items"], b["payment"])
        for i in b["items"]:
            if i.get("manual"):
                db.audit(user["username"], "manual_weight", f"sale {sid}: {i['name']} {i['qty']}kg")
        out = {"id": sid, "receipt": rcpt(sid), "printed": False}
        if db.get("auto_print") == "1":
            out.update(do_print(sid))
        return out
    if p == "/api/void":
        db.void_sale(b["id"], user["username"]); return {"ok": 1}
    if p == "/api/print":
        return do_print(b["id"])
    if p == "/api/receipt":
        return {"text": rcpt(q["id"])}
    if p == "/api/report":
        return {"report": db.z_report(q["from"], q["to"]), "sales": rows(db.sales(q["from"], q["to"]))}
    if p == "/api/export.csv":
        import tempfile
        tmp = tempfile.mktemp(suffix=".csv"); receipt.export_csv(db, q["from"], q["to"], tmp)
        data = open(tmp, "rb").read(); os.remove(tmp)
        return (data, "text/csv", {"Content-Disposition": f'attachment; filename="sales_{q["from"]}_{q["to"]}.csv"'})
    if p == "/api/stock":
        if b:
            db.stock_set(b["product_id"], b["day"], float(b["opening"] or 0), float(b["received"] or 0)); return {"ok": 1}
        return db.stock_rows(q["day"])

    # --- weighbridge ---
    if p == "/api/vehicle":
        return {"info": db.vehicle_info(q.get("no", ""))}
    if p == "/api/ticket/open":
        veh = (b.get("vehicle") or "").strip()
        if not veh:
            raise ValueError("Enter the vehicle number")
        ft, stored = b.get("first_type") or "gross", None
        if b.get("stored_tare"):
            stored, ft = db.vehicle_tare(veh), "gross"
            if stored is None:
                raise ValueError("No stored tare for this vehicle yet")
        w = take_weight()
        f = {k: (b.get(k) or "").strip() for k in ("customer", "driver", "trailer", "product", "destination", "remarks")}
        tid = db.create_ticket({**f, "vehicle": veh}, ft, w, user["username"], db.get("indicator"), db.get("ticket_prefix") or "WB-", stored)
        return tk(tid)
    if p == "/api/ticket/close":
        db.close_ticket(int(b["id"]), take_weight()); return tk(b["id"])
    if p == "/api/tickets/open":
        return db.open_tickets()
    if p == "/api/ticket":
        return tk(q["id"])
    if p == "/api/tickets":
        return db.tickets(q["from"], q["to"])
    if p == "/api/tickets/report":
        return db.ticket_report(q["from"], q["to"])
    if p == "/api/ticket/void":
        db.void_ticket(int(b["id"]), user["username"]); return {"ok": 1}
    if p == "/api/tickets.csv":
        cols = ["ticket_no", "first_ts", "customer", "driver", "vehicle", "trailer", "product", "destination", "gross_kg", "tare_kg", "net_kg", "wmode", "operator", "status", "remarks"]
        data = csv_bytes(cols, [[t[c] for c in cols] for t in reversed(db.tickets(q["from"], q["to"]))])
        return (data, "text/csv", {"Content-Disposition": f'attachment; filename="tickets_{q["from"]}_{q["to"]}.csv"'})

    # --- settings & users ---
    if p == "/api/settings":
        keys = ADMIN_KEYS + MANAGER_KEYS if me == "admin" else MANAGER_KEYS
        for k in keys:
            if k in b:
                v = str(b[k])
                if k == "biz_mode" and v not in allowed_biz(lic()["modes"]):
                    raise ValueError("That business type is not included in your license")
                if k == "decimals" and v not in "0123":
                    raise ValueError("Decimals must be 0 to 3")
                db.put(k, v)
        got = {k: db.get(k) for k in keys}
        if any(k in b for k in ("mode", "stable_tol", "scale_unit")) and me == "admin":
            start_scale()
        return got
    if p == "/api/users":
        return rows(db.users())
    if p == "/api/user":
        name, pw, role = b.get("username", "").strip(), b.get("password", ""), b.get("role", "operator")
        if role not in LEVEL or not name or len(pw) < 4:
            raise ValueError("Enter a username and a password of at least 4 characters")
        ex = db.q("SELECT role FROM users WHERE username=? AND active=1", (name,))
        if me != "admin" and (role != "operator" or (ex and ex[0]["role"] != "operator")):
            raise HTTPError(403, "Managers can only manage operators")
        if ex:
            db.set_password(name, pw)
            if me == "admin" and ex[0]["role"] != role and not (ex[0]["role"] == "admin" and db.admin_count() < 2):
                db.set_role(name, role)
        else:
            db.add_user(name, pw, role)
        db.audit(user["username"], "user_saved", name); return {"ok": 1}
    if p == "/api/user/delete":
        t = db.user_row(int(b["id"]))
        if not t or t["id"] == user["id"]:
            raise ValueError("You cannot remove your own account")
        if me != "admin" and t["role"] != "operator":
            raise HTTPError(403, "Managers can only manage operators")
        if t["role"] == "admin" and db.admin_count() < 2:
            raise ValueError("At least one administrator must remain")
        db.deactivate_user(t["id"]); db.audit(user["username"], "user_removed", t["username"]); return {"ok": 1}
    if p == "/api/license":
        return {**lic(), "machine_id": licensing.machine_id()}
    if p == "/api/license/change":
        licensing.activate(b.get("key", ""), LICFILE); lic(True); return boot(user)
    raise HTTPError(404, "Not found: " + p)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json", code=200, extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self.route({})

    def do_POST(self):
        self.route(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"))

    def route(self, body):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.reply(open(os.path.join(BASE, "index.html"), "rb").read(), "text/html; charset=utf-8")
            c = SimpleCookie(self.headers.get("Cookie", "")); tok = c["sid"].value if "sid" in c else None
            with dblock:
                res = handle(u.path, q, body, sessions.get(tok), tok)
            self.reply(res[0], res[1], extra=res[2]) if isinstance(res, tuple) else self.reply(res)
        except HTTPError as e:
            self.reply({"error": str(e), "need": e.need}, code=e.code)
        except (ValueError, KeyError) as e:
            self.reply({"error": ("Missing field: " + str(e)) if isinstance(e, KeyError) else str(e)}, code=400)
        except Exception as e:
            self.reply({"error": str(e)}, code=500)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "your-pc-ip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", action="store_true", help="open in browser instead of a desktop window")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    start_scale()
    url = f"http://127.0.0.1:{a.port}"
    print(f"Avena Smart Weighing POS running at {url}")
    if a.host == "0.0.0.0":
        print(f"Other devices on the same network: http://{lan_ip()}:{a.port}")
    if not a.browser:
        try:
            import webview
            webview.create_window("Avena Smart Weighing POS", url, width=1360, height=860, min_size=(1000, 650))
            webview.start(); scale.stop(); return
        except Exception as e:
            print("Desktop window unavailable (", e, ") - using browser. Install with: pip install pywebview")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scale.stop()


if __name__ == "__main__":
    main()
