import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import datetime as dt
from db import DB
from scale import ScaleReader, PROFILES, list_serial_ports, autodetect_port, parse_frame
import receipt

COL = {"stable": ("#1b8a3a", "STABLE"), "unstable": ("#d98a00", "UNSTABLE"),
       "no_data": ("#c0392b", "NO DATA FROM SCALE"), "disconnected": ("#c0392b", "SCALE DISCONNECTED")}


def today():
    return dt.date.today().isoformat()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Avena Smart Weighing POS")
        self.geometry("1200x740")
        self.db, self.scale = DB(), ScaleReader()
        self.user, self.cart, self.manual_next, self.tech_unlocked = None, [], False, False
        self.withdraw()
        if self.login():
            self.deiconify()
            self.build()
            self.start_scale()
            self.poll()
        else:
            self.destroy()

    # ---------- login ----------
    def login(self):
        d = tk.Toplevel(self); d.title("Login"); d.grab_set(); d.resizable(False, False)
        ttk.Label(d, text="Avena Smart Weighing POS", font=("Segoe UI", 14, "bold")).grid(row=0, columnspan=2, padx=20, pady=12)
        ttk.Label(d, text="Username").grid(row=1, column=0, padx=10, pady=4)
        ttk.Label(d, text="Password").grid(row=2, column=0, padx=10, pady=4)
        u, p = ttk.Entry(d), ttk.Entry(d, show="*")
        u.grid(row=1, column=1, padx=10); p.grid(row=2, column=1, padx=10); u.focus()

        def go(_=None):
            r = self.db.login(u.get().strip(), p.get())
            if r:
                self.user = r; d.destroy()
            else:
                messagebox.showerror("Login", "Wrong username or password", parent=d)
        ttk.Button(d, text="Login", command=go).grid(row=3, columnspan=2, pady=12)
        d.bind("<Return>", go)
        self.wait_window(d)
        return self.user is not None

    @property
    def admin(self):
        return self.user["role"] == "admin"

    # ---------- layout ----------
    def build(self):
        top = ttk.Frame(self); top.pack(fill="x")
        ttk.Label(top, text=f"{self.db.get('shop_name')}  |  {self.user['username']} ({self.user['role']})",
                  font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=4)
        ttk.Button(top, text="Logout", command=self.logout).pack(side="right", padx=8)
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True)
        self.t_checkout = ttk.Frame(self.nb); self.nb.add(self.t_checkout, text="Checkout")
        self.build_checkout()
        if self.admin:
            self.t_prod, self.t_stock = ttk.Frame(self.nb), ttk.Frame(self.nb)
            self.t_rep, self.t_set, self.t_tech = ttk.Frame(self.nb), ttk.Frame(self.nb), ttk.Frame(self.nb)
            for f, n in ((self.t_prod, "Products"), (self.t_stock, "Stock"), (self.t_rep, "Reports"),
                         (self.t_set, "Settings & Users"), (self.t_tech, "Technician")):
                self.nb.add(f, text=n)
            self.build_products(); self.build_stock(); self.build_reports(); self.build_settings(); self.build_tech()
            self.nb.bind("<<NotebookTabChanged>>", self.on_tab)

    def logout(self):
        self.scale.stop(); self.destroy()

    def on_tab(self, _):
        t = self.nb.select()
        if t == str(self.t_tech) and not self.tech_unlocked:
            pin = simpledialog.askstring("Technician", "Technician PIN:", show="*", parent=self)
            if pin == self.db.get("tech_pin"):
                self.tech_unlocked = True
            else:
                self.nb.select(0); return
        {str(self.t_stock): self.refresh_stock, str(self.t_rep): self.run_report,
         str(self.t_prod): self.refresh_products, str(self.t_checkout): self.refresh_grid}.get(t, lambda: None)()

    # ---------- scale ----------
    def start_scale(self):
        g = self.db.get
        self.scale.configure(g("profile"), g("custom_regex"), g("reverse") == "1", g("mode"))
        port = g("port") or autodetect_port()
        if port:
            self.scale.start(port, g("baud") or 9600)

    def poll(self):
        s = self.scale.status()
        color, text = COL[s]
        self.wlabel.config(text=f"{self.scale.weight:.3f} kg", foreground=color)
        self.slabel.config(text=text + (f"  ({self.scale.error[:40]})" if s == "disconnected" and self.scale.error else ""), foreground=color)
        if self.admin and hasattr(self, "rawbox") and self.nb.select() == str(self.t_tech):
            self.rawbox.delete("1.0", "end"); self.rawbox.insert("end", "\n".join(list(self.scale.raw)[-15:]))
        self.after(150, self.poll)

    # ---------- checkout ----------
    def build_checkout(self):
        f = self.t_checkout
        f.columnconfigure(0, weight=3); f.columnconfigure(1, weight=2); f.rowconfigure(0, weight=1)
        L, R = ttk.Frame(f), ttk.Frame(f)
        L.grid(row=0, column=0, sticky="nsew", padx=6, pady=6); R.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        box = tk.Frame(L, bg="black"); box.pack(fill="x")
        self.wlabel = tk.Label(box, text="0.000 kg", font=("Consolas", 54, "bold"), bg="black", fg="#1b8a3a"); self.wlabel.pack()
        self.slabel = tk.Label(box, text="", font=("Segoe UI", 12, "bold"), bg="black", fg="#c0392b"); self.slabel.pack(pady=(0, 6))
        self.wlabel.config(foreground="#1b8a3a")
        bar = ttk.Frame(L); bar.pack(fill="x", pady=6)
        ttk.Label(bar, text="PLU code:").pack(side="left")
        self.plu = ttk.Entry(bar, width=8); self.plu.pack(side="left", padx=4)
        self.plu.bind("<Return>", self.add_by_plu)
        if self.db.get("allow_manual") == "1":
            ttk.Button(bar, text="Manual weight (PIN)", command=self.arm_manual).pack(side="right")
        self.msg = tk.Label(L, text="", fg="#c0392b", font=("Segoe UI", 11, "bold")); self.msg.pack(fill="x")
        self.grid_f = ttk.Frame(L); self.grid_f.pack(fill="both", expand=True)
        self.refresh_grid()
        cols = ("item", "qty", "price", "total")
        self.tree = ttk.Treeview(R, columns=cols, show="headings", height=14)
        for c, w in zip(cols, (170, 80, 80, 90)):
            self.tree.heading(c, text=c.title()); self.tree.column(c, width=w, anchor="e" if c != "item" else "w")
        self.tree.pack(fill="both", expand=True)
        self.total_l = ttk.Label(R, text="TOTAL: KES 0.00", font=("Segoe UI", 20, "bold")); self.total_l.pack(pady=8)
        b = ttk.Frame(R); b.pack(fill="x")
        ttk.Button(b, text="Remove item", command=self.remove_item).pack(side="left")
        ttk.Button(b, text="Clear", command=self.clear_cart).pack(side="left", padx=4)
        ttk.Button(b, text="PAY  (F12)", command=self.pay).pack(side="right")
        self.bind("<F12>", lambda e: self.pay())

    def refresh_grid(self):
        for w in self.grid_f.winfo_children():
            w.destroy()
        for n, p in enumerate(self.db.products()):
            suffix = "/kg" if p["unit_type"] == "kg" else " each"
            tk.Button(self.grid_f, text=f"[{p['plu']}] {p['name']}\nKES {p['price']:g}{suffix}", font=("Segoe UI", 11, "bold"),
                      bg="#e8f1fb", height=3, command=lambda p=p: self.add_product(p)).grid(row=n // 4, column=n % 4, sticky="nsew", padx=3, pady=3)
        for c in range(4):
            self.grid_f.columnconfigure(c, weight=1)

    def flash(self, t):
        self.msg.config(text=t); self.after(3000, lambda: self.msg.config(text=""))

    def add_by_plu(self, _=None):
        code = self.plu.get().strip(); self.plu.delete(0, "end")
        for p in self.db.products():
            if p["plu"] == code:
                return self.add_product(p)
        self.flash(f"No product with PLU {code}")

    def arm_manual(self):
        if simpledialog.askstring("Admin override", "Admin override PIN:", show="*", parent=self) == self.db.get("override_pin"):
            self.manual_next = True; self.flash("Manual weight armed for next item")
        else:
            self.flash("Wrong PIN")

    def add_product(self, p):
        manual = 0
        if p["unit_type"] == "kg":
            if self.manual_next:
                q = simpledialog.askfloat("Manual weight", f"Weight in kg for {p['name']}:", minvalue=0.001, parent=self)
                self.manual_next = False
                if not q:
                    return
                manual = 1; self.db.audit(self.user["username"], "manual_weight", f"{p['name']} {q}kg")
            else:
                q = self.scale.take()
                if q is None:
                    return self.flash("Waiting for a stable weight on the scale…")
        else:
            q = simpledialog.askinteger("Quantity", f"How many {p['name']}?", minvalue=1, initialvalue=1, parent=self)
            if not q:
                return
        self.cart.append({"product_id": p["id"], "name": p["name"], "unit_type": p["unit_type"], "qty": q, "unit_price": p["price"], "manual": manual})
        self.refresh_cart()

    def refresh_cart(self):
        self.tree.delete(*self.tree.get_children())
        for i in self.cart:
            q = f"{i['qty']:.3f}kg" if i["unit_type"] == "kg" else f"{i['qty']:g}"
            self.tree.insert("", "end", values=(i["name"] + (" *" if i["manual"] else ""), q, f"{i['unit_price']:,.2f}", f"{i['qty'] * i['unit_price']:,.2f}"))
        self.total_l.config(text=f"TOTAL: KES {self.total():,.2f}")

    def total(self):
        return round(sum(round(i["qty"] * i["unit_price"], 2) for i in self.cart), 2)

    def remove_item(self):
        s = self.tree.selection()
        if s:
            del self.cart[self.tree.index(s[0])]; self.refresh_cart()

    def clear_cart(self):
        self.cart = []; self.refresh_cart()

    def pay(self):
        if not self.cart:
            return
        total = self.total()
        d = tk.Toplevel(self); d.title("Payment"); d.grab_set()
        ttk.Label(d, text=f"Total: KES {total:,.2f}", font=("Segoe UI", 18, "bold")).grid(row=0, columnspan=2, padx=20, pady=10)
        m = ttk.Combobox(d, values=["Cash", "M-Pesa", "Split (Cash + M-Pesa)", "Credit"], state="readonly"); m.set("Cash")
        m.grid(row=1, columnspan=2, pady=4)
        ent = {}
        for r, (k, lbl) in enumerate((("cash", "Cash given"), ("mpesa", "M-Pesa amount"), ("ref", "M-Pesa reference"), ("cust", "Customer (credit)")), 2):
            ttk.Label(d, text=lbl).grid(row=r, column=0, padx=10, pady=3, sticky="e")
            ent[k] = ttk.Entry(d); ent[k].grid(row=r, column=1, padx=10)
        info = ttk.Label(d, text="", font=("Segoe UI", 12, "bold")); info.grid(row=6, columnspan=2, pady=6)
        ent["cash"].focus()

        def num(k):
            try:
                return float(ent[k].get() or 0)
            except ValueError:
                return 0.0

        def confirm():
            mode, pay = m.get(), {}
            if mode == "Cash":
                if num("cash") < total:
                    return info.config(text="Cash given is less than total", foreground="red")
                pay = {"cash": total, "tendered": num("cash"), "change": round(num("cash") - total, 2)}
            elif mode == "M-Pesa":
                if not ent["ref"].get().strip():
                    return info.config(text="Enter M-Pesa reference", foreground="red")
                pay = {"mpesa": total, "ref": ent["ref"].get().strip()}
            elif mode.startswith("Split"):
                if abs(num("cash") + num("mpesa") - total) > 0.01 or not ent["ref"].get().strip():
                    return info.config(text="Cash + M-Pesa must equal total, with reference", foreground="red")
                pay = {"cash": num("cash"), "mpesa": num("mpesa"), "ref": ent["ref"].get().strip()}
            else:
                if not ent["cust"].get().strip():
                    return info.config(text="Enter customer name", foreground="red")
                pay = {"credit": total, "customer": ent["cust"].get().strip()}
            sid = self.db.create_sale(self.user["id"], self.cart, pay)
            self.clear_cart(); d.destroy(); self.after_sale(sid, pay.get("change", 0))
        ttk.Button(d, text="Confirm", command=confirm).grid(row=7, columnspan=2, pady=10)
        d.bind("<Return>", lambda e: confirm())

    def after_sale(self, sid, change):
        if change:
            messagebox.showinfo("Change", f"Give change: KES {change:,.2f}", parent=self)
        if self.db.get("auto_print") == "1":
            self.print_receipt(sid)
        else:
            self.flash(f"Sale #{sid} saved")

    def shop(self):
        return {"name": self.db.get("shop_name"), "address": self.db.get("shop_address")}

    def print_receipt(self, sid):
        sale, items = self.db.sale(sid)
        w = int(self.db.get("receipt_width") or 32)
        try:
            receipt.print_raw(self.db.get("printer"), receipt.receipt_bytes(self.shop(), sale, items, w))
        except Exception as e:
            messagebox.showwarning("Printer", f"Could not print ({e}). Sale is saved.\n\n" + receipt.receipt_text(self.shop(), sale, items, w), parent=self)

    # ---------- products ----------
    def build_products(self):
        f = self.t_prod
        self.ptree = ttk.Treeview(f, columns=("plu", "name", "type", "price"), show="headings", height=16)
        for c in ("plu", "name", "type", "price"):
            self.ptree.heading(c, text=c.title())
        self.ptree.pack(fill="both", expand=True, padx=8, pady=8)
        self.ptree.bind("<<TreeviewSelect>>", self.fill_pform)
        fm = ttk.Frame(f); fm.pack(fill="x", padx=8, pady=6)
        self.pv = {k: tk.StringVar() for k in ("plu", "name", "type", "price")}; self.pv["type"].set("kg"); self.pid = None
        for k, w in (("plu", 6), ("name", 24), ("price", 10)):
            ttk.Label(fm, text=k.title()).pack(side="left"); ttk.Entry(fm, textvariable=self.pv[k], width=w).pack(side="left", padx=4)
        ttk.Combobox(fm, textvariable=self.pv["type"], values=["kg", "fixed"], width=6, state="readonly").pack(side="left")
        for t, c in (("Save", self.save_prod), ("New", self.new_prod), ("Delete", self.del_prod)):
            ttk.Button(fm, text=t, command=c).pack(side="left", padx=4)
        self.refresh_products()

    def refresh_products(self):
        self.ptree.delete(*self.ptree.get_children())
        for p in self.db.products():
            self.ptree.insert("", "end", iid=str(p["id"]), values=(p["plu"], p["name"], p["unit_type"], f"{p['price']:g}"))

    def fill_pform(self, _):
        s = self.ptree.selection()
        if s:
            v = self.ptree.item(s[0])["values"]; self.pid = int(s[0])
            for k, x in zip(("plu", "name", "type", "price"), v):
                self.pv[k].set(x)

    def new_prod(self):
        self.pid = None
        for k in self.pv:
            self.pv[k].set("kg" if k == "type" else "")

    def save_prod(self):
        try:
            self.db.save_product(self.pid, self.pv["plu"].get().strip(), self.pv["name"].get().strip(), self.pv["type"].get(), float(self.pv["price"].get()))
            self.db.audit(self.user["username"], "save_product", self.pv["name"].get())
        except Exception as e:
            return messagebox.showerror("Product", f"Could not save: {e}")
        self.new_prod(); self.refresh_products()

    def del_prod(self):
        if self.pid and messagebox.askyesno("Delete", "Remove this product?"):
            self.db.delete_product(self.pid); self.new_prod(); self.refresh_products()

    # ---------- stock ----------
    def build_stock(self):
        f = self.t_stock
        top = ttk.Frame(f); top.pack(fill="x", padx=8, pady=8)
        self.sday = tk.StringVar(value=today())
        ttk.Label(top, text="Date").pack(side="left"); ttk.Entry(top, textvariable=self.sday, width=12).pack(side="left", padx=4)
        ttk.Button(top, text="Load", command=self.refresh_stock).pack(side="left")
        cols = ("name", "opening", "received", "sold", "expected")
        self.stree = ttk.Treeview(f, columns=cols, show="headings", height=14)
        for c in cols:
            self.stree.heading(c, text={"expected": "Expected closing (kg)"}.get(c, c.title()))
        self.stree.pack(fill="both", expand=True, padx=8)
        fm = ttk.Frame(f); fm.pack(fill="x", padx=8, pady=8)
        self.sv = {"open": tk.StringVar(), "recv": tk.StringVar()}
        ttk.Label(fm, text="Select a product above, then:  Opening kg").pack(side="left")
        ttk.Entry(fm, textvariable=self.sv["open"], width=8).pack(side="left", padx=4)
        ttk.Label(fm, text="Received kg").pack(side="left")
        ttk.Entry(fm, textvariable=self.sv["recv"], width=8).pack(side="left", padx=4)
        ttk.Button(fm, text="Save", command=self.save_stock).pack(side="left")
        self.stree.bind("<<TreeviewSelect>>", lambda e: self._sel_stock())
        self.refresh_stock()

    def refresh_stock(self):
        self.stree.delete(*self.stree.get_children())
        for r in self.db.stock_rows(self.sday.get()):
            self.stree.insert("", "end", iid=str(r["id"]), values=(r["name"], r["opening"], r["received"], r["sold"], r["expected"]))

    def _sel_stock(self):
        s = self.stree.selection()
        if s:
            v = self.stree.item(s[0])["values"]; self.sv["open"].set(v[1]); self.sv["recv"].set(v[2])

    def save_stock(self):
        s = self.stree.selection()
        if s:
            try:
                self.db.stock_set(int(s[0]), self.sday.get(), float(self.sv["open"].get() or 0), float(self.sv["recv"].get() or 0))
            except ValueError:
                return messagebox.showerror("Stock", "Enter numbers only")
            self.refresh_stock()

    # ---------- reports ----------
    def build_reports(self):
        f = self.t_rep
        top = ttk.Frame(f); top.pack(fill="x", padx=8, pady=8)
        self.d1, self.d2 = tk.StringVar(value=today()), tk.StringVar(value=today())
        for l, v in (("From", self.d1), ("To", self.d2)):
            ttk.Label(top, text=l).pack(side="left"); ttk.Entry(top, textvariable=v, width=12).pack(side="left", padx=4)
        for t, c in (("Run", self.run_report), ("Export CSV (Excel)", self.export), ("Print Z-report", self.print_z), ("Reprint receipt", self.reprint), ("Void sale", self.void)):
            ttk.Button(top, text=t, command=c).pack(side="left", padx=3)
        pw = ttk.PanedWindow(f, orient="horizontal"); pw.pack(fill="both", expand=True, padx=8, pady=4)
        self.zbox = tk.Text(pw, font=("Consolas", 10), width=55); pw.add(self.zbox)
        cols = ("id", "time", "by", "total", "status")
        self.rtree = ttk.Treeview(pw, columns=cols, show="headings")
        for c, w in zip(cols, (50, 140, 80, 90, 70)):
            self.rtree.heading(c, text=c.title()); self.rtree.column(c, width=w)
        pw.add(self.rtree)

    def run_report(self):
        rep = self.db.z_report(self.d1.get(), self.d2.get())
        self.zbox.delete("1.0", "end"); self.zbox.insert("end", receipt.z_text(rep, self.db.get("shop_name")))
        self.rtree.delete(*self.rtree.get_children())
        for s in self.db.sales(self.d1.get(), self.d2.get()):
            self.rtree.insert("", "end", iid=str(s["id"]), values=(s["id"], s["ts"], s["username"], f"{s['total']:,.2f}", "VOID" if s["voided"] else "ok"))

    def export(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"sales_{self.d1.get()}_{self.d2.get()}.csv")
        if p:
            receipt.export_csv(self.db, self.d1.get(), self.d2.get(), p); messagebox.showinfo("Export", "Saved. Open it in Excel.")

    def print_z(self):
        txt = receipt.z_text(self.db.z_report(self.d1.get(), self.d2.get()), self.db.get("shop_name"), int(self.db.get("receipt_width") or 32))
        try:
            receipt.print_raw(self.db.get("printer"), receipt.ESC_INIT + txt.encode("cp437", "replace") + b"\n\n\n" + receipt.CUT)
        except Exception as e:
            messagebox.showwarning("Printer", f"Could not print: {e}")

    def reprint(self):
        s = self.rtree.selection()
        if s:
            self.print_receipt(int(s[0]))

    def void(self):
        s = self.rtree.selection()
        if s and messagebox.askyesno("Void", f"Void sale #{s[0]}? This removes it from totals and stock."):
            self.db.void_sale(int(s[0]), self.user["username"]); self.run_report()

    # ---------- settings & users ----------
    def build_settings(self):
        f = self.t_set
        box = ttk.LabelFrame(f, text="Shop & receipt"); box.pack(fill="x", padx=8, pady=8)
        self.setv = {}
        for r, (k, l) in enumerate((("shop_name", "Shop name"), ("shop_address", "Address/phone"), ("printer", "Windows printer name"),
                                    ("receipt_width", "Receipt width (32=58mm, 48=80mm)"), ("auto_print", "Auto-print receipt (1/0)"),
                                    ("allow_manual", "Allow manual weight w/ PIN (1/0)"), ("override_pin", "Admin override PIN"), ("tech_pin", "Technician PIN"))):
            ttk.Label(box, text=l).grid(row=r, column=0, sticky="e", padx=6, pady=2)
            self.setv[k] = tk.StringVar(value=self.db.get(k)); ttk.Entry(box, textvariable=self.setv[k], width=34).grid(row=r, column=1, pady=2)
        ttk.Button(box, text="Save settings (restart to apply)", command=lambda: [self.db.put(k, v.get()) for k, v in self.setv.items()] and messagebox.showinfo("Saved", "Saved")).grid(row=9, columnspan=2, pady=6)
        ub = ttk.LabelFrame(f, text="Users"); ub.pack(fill="both", expand=True, padx=8, pady=8)
        self.utree = ttk.Treeview(ub, columns=("user", "role"), show="headings", height=6)
        self.utree.heading("user", text="Username"); self.utree.heading("role", text="Role"); self.utree.pack(fill="x")
        fm = ttk.Frame(ub); fm.pack(fill="x", pady=6)
        self.uv = [tk.StringVar(), tk.StringVar(), tk.StringVar(value="attendant")]
        ttk.Entry(fm, textvariable=self.uv[0], width=14).pack(side="left", padx=3)
        ttk.Entry(fm, textvariable=self.uv[1], width=14, show="*").pack(side="left", padx=3)
        ttk.Combobox(fm, textvariable=self.uv[2], values=["attendant", "admin"], width=10, state="readonly").pack(side="left")
        ttk.Button(fm, text="Add / reset password", command=self.save_user).pack(side="left", padx=4)
        ttk.Button(fm, text="Remove selected", command=self.del_user).pack(side="left")
        self.refresh_users()

    def refresh_users(self):
        self.utree.delete(*self.utree.get_children())
        for u in self.db.users():
            self.utree.insert("", "end", iid=str(u["id"]), values=(u["username"], u["role"]))

    def save_user(self):
        u, p, r = self.uv[0].get().strip(), self.uv[1].get(), self.uv[2].get()
        if not u or len(p) < 4:
            return messagebox.showerror("User", "Username required, password at least 4 characters")
        if self.db.q("SELECT 1 FROM users WHERE username=? AND active=1", (u,)):
            self.db.set_password(u, p)
        else:
            self.db.add_user(u, p, r)
        self.uv[1].set(""); self.refresh_users()

    def del_user(self):
        s = self.utree.selection()
        if s and self.utree.item(s[0])["values"][0] != self.user["username"]:
            self.db.deactivate_user(int(s[0])); self.refresh_users()

    # ---------- technician ----------
    def build_tech(self):
        f = self.t_tech
        r1 = ttk.Frame(f); r1.pack(fill="x", padx=8, pady=8)
        g = self.db.get
        self.tv = {"port": tk.StringVar(value=g("port")), "baud": tk.StringVar(value=g("baud")), "profile": tk.StringVar(value=g("profile")),
                   "mode": tk.StringVar(value=g("mode")), "regex": tk.StringVar(value=g("custom_regex")), "rev": tk.IntVar(value=int(g("reverse") or 0))}
        self.portcb = ttk.Combobox(r1, textvariable=self.tv["port"], width=10); self.portcb.pack(side="left")
        ttk.Button(r1, text="Refresh ports", command=self.refresh_ports).pack(side="left", padx=3)
        ttk.Combobox(r1, textvariable=self.tv["baud"], values=["1200", "2400", "4800", "9600", "19200"], width=7).pack(side="left", padx=3)
        ttk.Combobox(r1, textvariable=self.tv["profile"], values=list(PROFILES), width=28, state="readonly").pack(side="left", padx=3)
        ttk.Combobox(r1, textvariable=self.tv["mode"], values=["continuous", "button"], width=11, state="readonly").pack(side="left", padx=3)
        r2 = ttk.Frame(f); r2.pack(fill="x", padx=8)
        ttk.Label(r2, text="Custom regex (groups: st, w, u)").pack(side="left")
        ttk.Entry(r2, textvariable=self.tv["regex"], width=60).pack(side="left", padx=4)
        ttk.Checkbutton(r2, text="Reverse digits", variable=self.tv["rev"]).pack(side="left")
        r3 = ttk.Frame(f); r3.pack(fill="x", padx=8, pady=8)
        for t, c in (("Save & Connect", self.tech_connect), ("Disconnect", self.scale.stop), ("Test regex on last frame", self.tech_test)):
            ttk.Button(r3, text=t, command=c).pack(side="left", padx=3)
        self.simw = tk.StringVar(value="0")
        ttk.Button(r3, text="Start simulator (no hardware)", command=self.tech_sim).pack(side="left", padx=12)
        ttk.Entry(r3, textvariable=self.simw, width=7).pack(side="left"); ttk.Label(r3, text="kg").pack(side="left")
        self.simw.trace_add("write", lambda *a: self._setsim())
        self.testl = ttk.Label(f, text=""); self.testl.pack(anchor="w", padx=8)
        ttk.Label(f, text="Raw frames from scale (live):").pack(anchor="w", padx=8)
        self.rawbox = tk.Text(f, height=16, font=("Consolas", 10)); self.rawbox.pack(fill="both", expand=True, padx=8, pady=6)
        self.refresh_ports()

    def refresh_ports(self):
        self.portcb["values"] = [p for p, _ in list_serial_ports()]

    def _setsim(self):
        try:
            self.scale.sim_weight = float(self.simw.get() or 0)
        except ValueError:
            pass

    def tech_sim(self):
        self.scale.configure("Generic (ST,GS,+001.250kg)"); self.scale.start_sim()

    def tech_connect(self):
        for k, key in (("port", "port"), ("baud", "baud"), ("profile", "profile"), ("mode", "mode"), ("regex", "custom_regex")):
            self.db.put(key, self.tv[k].get())
        self.db.put("reverse", self.tv["rev"].get())
        self.start_scale()

    def tech_test(self):
        prof = PROFILES[self.tv["profile"].get()]
        rx = prof["regex"] or self.tv["regex"].get()
        rev = prof["reverse"] if prof["regex"] else bool(self.tv["rev"].get())
        last = self.scale.raw[-1] if self.scale.raw else ""
        self.testl.config(text=f"Frame {last!r} -> {parse_frame(last, rx, rev)}  (weight_kg, stable flag)")
