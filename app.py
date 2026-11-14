"""Avena Smart Weighing POS: scale engine + local web server + optional desktop window.
   python app.py            -> desktop window (needs pywebview) or your browser
   python app.py --browser  -> browser only
   python app.py --host 0.0.0.0  -> also open from phones/tablets/other PCs on the same Wi-Fi
"""
import argparse, json, os, socket, sys, tempfile, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from db import DB
from scale import ScaleReader, PROFILES, list_serial_ports, autodetect_port
import receipt

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
KEYS = ["shop_name", "shop_address", "printer", "receipt_width", "auto_print", "port", "baud", "profile", "mode", "custom_regex", "reverse"]
db, scale, dblock, demo = DB(), ScaleReader(), threading.RLock(), False


def start_scale():
    global demo
    g = db.get
    scale.configure(g("profile"), g("custom_regex"), g("reverse") == "1", g("mode"))
    port = g("port") or autodetect_port()
    if port and g("demo") != "1":
        scale.start(port, g("baud") or 9600); demo = False
    else:  # no scale plugged in -> built-in demo scale
        scale.configure("Generic (ST,GS,+001.250kg)", mode="continuous"); scale.start_sim(); demo = True


def rows(r):
    return [dict(x) for x in r]


def shop():
    return {"name": db.get("shop_name"), "address": db.get("shop_address")}


def rcpt(sid):
    s, it = db.sale(int(sid))
    return receipt.receipt_text(shop(), s, it, int(db.get("receipt_width") or 32))


def do_print(sid):
    try:
        s, it = db.sale(int(sid))
        receipt.print_raw(db.get("printer"), receipt.receipt_bytes(shop(), s, it, int(db.get("receipt_width") or 32)))
        return {"printed": True}
    except Exception as e:
        return {"printed": False, "print_error": str(e)}


def handle(p, q, b):
    if p == "/api/scale":
        return {"weight": scale.weight, "status": scale.status(), "demo": demo, "port": db.get("port"), "error": scale.error, "raw": list(scale.raw)[-12:]}
    if p == "/api/take":
        return {"weight": scale.take()}
    if p == "/api/products":
        return rows(db.products())
    if p == "/api/product":
        db.save_product(b.get("id"), str(b["plu"]).strip(), b["name"].strip(), b["unit_type"], float(b["price"])); return {"ok": 1}
    if p == "/api/product/delete":
        db.delete_product(b["id"]); return {"ok": 1}
    if p == "/api/sale":
        sid = db.create_sale(None, b["items"], b["payment"])
        for i in b["items"]:
            if i.get("manual"):
                db.audit("-", "manual_weight", f"sale {sid}: {i['name']} {i['qty']}kg")
        out = {"id": sid, "receipt": rcpt(sid), "printed": False}
        if db.get("auto_print") == "1":
            out.update(do_print(sid))
        return out
    if p == "/api/void":
        db.void_sale(b["id"], "-"); return {"ok": 1}
    if p == "/api/print":
        return do_print(b["id"])
    if p == "/api/receipt":
        return {"text": rcpt(q["id"])}
    if p == "/api/report":
        return {"report": db.z_report(q["from"], q["to"]), "sales": rows(db.sales(q["from"], q["to"]))}
    if p == "/api/export.csv":
        tmp = tempfile.mktemp(suffix=".csv"); receipt.export_csv(db, q["from"], q["to"], tmp)
        data = open(tmp, "rb").read(); os.remove(tmp)
        return (data, "text/csv", {"Content-Disposition": f'attachment; filename="sales_{q["from"]}_{q["to"]}.csv"'})
    if p == "/api/stock":
        if b:
            db.stock_set(b["product_id"], b["day"], float(b["opening"] or 0), float(b["received"] or 0)); return {"ok": 1}
        return db.stock_rows(q["day"])
    if p == "/api/settings":
        for k in KEYS:
            if k in b:
                db.put(k, b[k])
        return {k: db.get(k) for k in KEYS}
    if p == "/api/ports":
        return {"ports": [{"device": d, "desc": x} for d, x in list_serial_ports()], "profiles": list(PROFILES)}
    if p == "/api/scale/connect":
        for k in ("port", "baud", "profile", "mode", "custom_regex", "reverse"):
            if k in b:
                db.put(k, b[k])
        db.put("demo", "0"); start_scale(); return {"ok": 1, "demo": demo}
    if p == "/api/scale/demo":
        db.put("demo", "1"); start_scale(); return {"ok": 1}
    if p == "/api/scale/sim":
        if not demo:
            db.put("demo", "1"); start_scale()
        scale.sim_weight = float(b.get("weight", 0)); scale.sim_settle = time.time() + 1.0
        return {"ok": 1}
    raise KeyError("not found: " + p)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json", code=200, extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
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
            with dblock:
                res = handle(u.path, q, body)
            self.reply(res[0], res[1], extra=res[2]) if isinstance(res, tuple) else self.reply(res)
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
        print(f"Other devices on the same Wi-Fi: http://{lan_ip()}:{a.port}")
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
