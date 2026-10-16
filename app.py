import os, sqlite3, secrets
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("STORE_SECRET_KEY") or secrets.token_hex(32)
DB_PATH = os.environ.get("DATABASE_PATH", "store.db")
UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
STATUSES = ["নতুন", "প্রসেসিং", "পাঠানো হয়েছে", "ডেলিভারি হয়েছে", "বাতিল"]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category_id INTEGER,
            description TEXT DEFAULT '', price REAL NOT NULL DEFAULT 0, discount REAL NOT NULL DEFAULT 0,
            stock INTEGER NOT NULL DEFAULT 0, image TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, discount_type TEXT NOT NULL DEFAULT 'percent',
            amount REAL NOT NULL DEFAULT 0, min_order REAL NOT NULL DEFAULT 0, expires TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, total REAL NOT NULL,
            delivery_fee REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'নতুন', payment_method TEXT NOT NULL DEFAULT 'COD',
            created_at TEXT NOT NULL, FOREIGN KEY(customer_id) REFERENCES customers(id));
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, product_id INTEGER,
            product_name TEXT NOT NULL, qty INTEGER NOT NULL, unit_price REAL NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        """)
        defaults = {"store_name":"মলে মালা ভান্ডার", "store_phone":"", "store_address":"", "delivery_fee":"25", "free_delivery_threshold":"499"}
        for k,v in defaults.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
        for name in ["চাল ও ডাল","তেল ও মশলা","প্যাকেটজাত খাবার","বেভারেজ","বিস্কুট ও স্ন্যাকস","গৃহস্থালি","ফল ও সবজি"]:
            con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)",(name,))
if not con.execute("SELECT key FROM settings WHERE key='admin_hash'").fetchone():
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('admin_hash',?)",(generate_password_hash("ChangeMe123!"),))
        if con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            seeds = [
              ("আতপ চাল (১ কেজি)","চাল ও ডাল",45,0,50,""),
              ("সরিষার তেল (১ লিটার)","তেল ও মশলা",150,0,30,""),
              ("মসুর ডাল (১ কেজি)","চাল ও ডাল",110,5,40,""),
              ("ম্যাগি নুডলস","প্যাকেটজাত খাবার",20,0,100,""),
              ("চা বিস্কুট","বিস্কুট ও স্ন্যাকস",25,0,70,"")]
            for n,c,p,d,s,img in seeds:
                cid=con.execute("SELECT id FROM categories WHERE name=?",(c,)).fetchone()[0]
                con.execute("INSERT INTO products(name,category_id,price,discount,stock,image) VALUES(?,?,?,?,?,?)",(n,cid,p,d,s,img))

def setting(key, default=""):
    with db() as con:
        row=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def allowed_file(filename):
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

@app.context_processor
def inject_store():
    return {"store_name": setting("store_name","মলে মালা ভান্ডার")}

@app.route("/")
def index():
    q=request.args.get("q","").strip()
    category=request.args.get("category","")
    with db() as con:
        cats=con.execute("SELECT * FROM categories ORDER BY name").fetchall()
        sql="""SELECT p.*, c.name AS category_name FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1"""
        args=[]
        if q:
            sql+=" AND (p.name LIKE ? OR p.description LIKE ?)"; args += [f"%{q}%",f"%{q}%"]
        if category.isdigit():
            sql+=" AND p.category_id=?"; args.append(int(category))
        products=con.execute(sql+" ORDER BY p.id DESC",args).fetchall()
    return render_template("index.html", products=products, categories=cats, selected_category=category, q=q,
                           delivery_fee=float(setting("delivery_fee","25")), free_threshold=float(setting("free_delivery_threshold","499")))

@app.route("/checkout", methods=["GET","POST"])
def checkout():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        phone=request.form.get("phone","").strip()
        address=request.form.get("address","").strip()
        raw=request.form.get("items_json","")
        import json
        try: items=json.loads(raw)
        except Exception: items=[]
        if not name or not phone or not address or not isinstance(items,list) or not items:
            flash("নাম, ফোন, ঠিকানা এবং কার্টের পণ্য দিন।","error")
            return redirect(url_for("index"))
        clean=[]; subtotal=0
        with db() as con:
            for item in items:
                try: pid=int(item.get("id")); qty=max(1,min(99,int(item.get("qty",1))))
                except Exception: continue
                p=con.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
                if not p or p["stock"] < qty: continue
                price=round(p["price"]*(1-p["discount"]/100),2)
                subtotal += price*qty; clean.append((p,qty,price))
            if not clean:
                flash("কার্টের পণ্য stock-এ নেই। আবার চেষ্টা করুন।","error")
                return redirect(url_for("index"))
            fee=0 if subtotal>=float(setting("free_delivery_threshold","499")) else float(setting("delivery_fee","25"))
            cur=con.execute("INSERT INTO customers(name,phone,address,created_at) VALUES(?,?,?,?)",(name,phone,address,datetime.now().isoformat(timespec="seconds")))
            customer_id=cur.lastrowid
            cur=con.execute("INSERT INTO orders(customer_id,total,delivery_fee,status,payment_method,created_at) VALUES(?,?,?,?,?,?)",
                            (customer_id,round(subtotal+fee,2),fee,"নতুন","COD",datetime.now().isoformat(timespec="seconds")))
            order_id=cur.lastrowid
            for p,qty,price in clean:
                con.execute("INSERT INTO order_items(order_id,product_id,product_name,qty,unit_price) VALUES(?,?,?,?,?)",(order_id,p["id"],p["name"],qty,price))
                con.execute("UPDATE products SET stock=stock-? WHERE id=?",(qty,p["id"]))
        return render_template("order_success.html", order_id=order_id, total=round(subtotal+fee,2))
    return render_template("checkout.html", delivery_fee=setting("delivery_fee","25"), free_threshold=setting("free_delivery_threshold","499"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    with db() as con:
        products=con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        orders=con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        revenue=con.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status!='বাতিল'").fetchone()[0]
        customers=con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        recent=con.execute("""SELECT o.*, c.name AS customer_name, c.phone FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC LIMIT 8""").fetchall()
    return render_template("admin.html", section="dashboard", products_count=products, orders_count=orders, revenue=revenue, customers_count=customers, recent=recent)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if check_password_hash(setting("admin_hash"), request.form.get("password","")):
            session.clear(); session["admin"]=True
            return redirect(url_for("admin_dashboard"))
        flash("পাসওয়ার্ড সঠিক নয়।","error")
    return render_template("login.html")

@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/admin/products", methods=["GET","POST"])
@admin_required
def admin_products():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        try:
            price=float(request.form.get("price","0")); discount=float(request.form.get("discount","0")); stock=int(request.form.get("stock","0"))
            cat=int(request.form.get("category_id") or 0)
        except ValueError:
            flash("দাম, discount ও stock সঠিকভাবে লিখুন।","error"); return redirect(url_for("admin_products"))
        if not name or price<0 or not 0<=discount<=100 or stock<0:
            flash("পণ্যের তথ্য যাচাই করুন।","error"); return redirect(url_for("admin_products"))
        image=""
        f=request.files.get("image")
        if f and f.filename and allowed_file(f.filename):
            filename=secrets.token_hex(8)+"_"+secure_filename(f.filename)
            f.save(os.path.join(UPLOAD_DIR,filename)); image=filename
        with db() as con:
            con.execute("INSERT INTO products(name,category_id,description,price,discount,stock,image) VALUES(?,?,?,?,?,?,?)",
                        (name,cat or None,request.form.get("description","").strip(),price,discount,stock,image))
        flash("পণ্য যোগ হয়েছে।","success"); return redirect(url_for("admin_products"))
    with db() as con:
        products=con.execute("SELECT p.*, c.name category_name FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.id DESC").fetchall()
        cats=con.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template("admin.html",section="products",products=products,categories=cats)

@app.route("/admin/products/<int:pid>/edit", methods=["POST"])
@admin_required
def edit_product(pid):
    try:
        price=float(request.form.get("price","0")); discount=float(request.form.get("discount","0")); stock=int(request.form.get("stock","0")); cat=int(request.form.get("category_id") or 0)
    except ValueError:
        flash("দাম/discount/stock সঠিক নয়।","error"); return redirect(url_for("admin_products"))
    name=request.form.get("name","").strip()
    if not name or price<0 or not 0<=discount<=100 or stock<0: 
        flash("পণ্যের তথ্য যাচাই করুন।","error"); return redirect(url_for("admin_products"))
    with db() as con:
        con.execute("UPDATE products SET name=?,category_id=?,description=?,price=?,discount=?,stock=? WHERE id=?",
                    (name,cat or None,request.form.get("description",""),price,discount,stock,pid))
        f=request.files.get("image")
        if f and f.filename and allowed_file(f.filename):
            filename=secrets.token_hex(8)+"_"+secure_filename(f.filename); f.save(os.path.join(UPLOAD_DIR,filename))
            con.execute("UPDATE products SET image=? WHERE id=?",(filename,pid))
    flash("পণ্য আপডেট হয়েছে।","success"); return redirect(url_for("admin_products"))

@app.route("/admin/products/<int:pid>/delete", methods=["POST"])
@admin_required
def delete_product(pid):
    with db() as con: con.execute("UPDATE products SET active=0 WHERE id=?",(pid,))
    flash("পণ্য storefront থেকে সরানো হয়েছে।","success"); return redirect(url_for("admin_products"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    with db() as con:
        orders=con.execute("SELECT o.*,c.name customer_name,c.phone,c.address FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC").fetchall()
        for order in orders:
            order_items=con.execute("SELECT * FROM order_items WHERE order_id=?",(order["id"],)).fetchall()
            # sqlite Row is immutable; map below in template using separate query map
        items={o["id"]:con.execute("SELECT * FROM order_items WHERE order_id=?",(o["id"],)).fetchall() for o in orders}
    return render_template("admin.html",section="orders",orders=orders,items=items,statuses=STATUSES)

@app.route("/admin/orders/<int:oid>/status", methods=["POST"])
@admin_required
def update_order_status(oid):
    status=request.form.get("status","")
    if status not in STATUSES: abort(400)
    with db() as con: con.execute("UPDATE orders SET status=? WHERE id=?",(status,oid))
    flash("অর্ডারের status আপডেট হয়েছে।","success"); return redirect(url_for("admin_orders"))

@app.route("/admin/categories", methods=["GET","POST"])
@admin_required
def admin_categories():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if name:
            try:
                with db() as con: con.execute("INSERT INTO categories(name) VALUES(?)",(name,))
                flash("ক্যাটাগরি যোগ হয়েছে।","success")
            except sqlite3.IntegrityError: flash("এই ক্যাটাগরি আগে থেকেই আছে।","error")
        return redirect(url_for("admin_categories"))
    with db() as con: cats=con.execute("SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category_id=c.id AND p.active=1) product_count FROM categories c ORDER BY name").fetchall()
    return render_template("admin.html",section="categories",categories=cats)

@app.route("/admin/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def delete_category(cid):
    with db() as con: con.execute("DELETE FROM categories WHERE id=?",(cid,))
    flash("ক্যাটাগরি মুছে ফেলা হয়েছে।","success"); return redirect(url_for("admin_categories"))

@app.route("/admin/customers")
@admin_required
def admin_customers():
    with db() as con:
        customers=con.execute("""SELECT c.*,COUNT(o.id) order_count,COALESCE(SUM(CASE WHEN o.status!='বাতিল' THEN o.total ELSE 0 END),0) total_spent FROM customers c LEFT JOIN orders o ON o.customer_id=c.id GROUP BY c.id ORDER BY c.id DESC""").fetchall()
    return render_template("admin.html",section="customers",customers=customers)

@app.route("/admin/coupons", methods=["GET","POST"])
@admin_required
def admin_coupons():
    if request.method=="POST":
        code=request.form.get("code","").strip().upper()
        try: amount=float(request.form.get("amount","0")); minimum=float(request.form.get("min_order","0"))
        except ValueError: flash("Coupon amount ও minimum order সঠিক দিন।","error"); return redirect(url_for("admin_coupons"))
        dtype=request.form.get("discount_type","percent"); expires=request.form.get("expires","")
        if not code or amount<=0 or minimum<0 or dtype not in ("percent","flat") or (dtype=="percent" and amount>100):
            flash("Coupon-এর তথ্য যাচাই করুন।","error"); return redirect(url_for("admin_coupons"))
        try:
            with db() as con: con.execute("INSERT INTO coupons(code,discount_type,amount,min_order,expires) VALUES(?,?,?,?,?)",(code,dtype,amount,minimum,expires))
            flash("Coupon তৈরি হয়েছে।","success")
        except sqlite3.IntegrityError: flash("এই coupon code আগে থেকেই আছে।","error")
        return redirect(url_for("admin_coupons"))
    with db() as con: coupons=con.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    return render_template("admin.html",section="coupons",coupons=coupons)

@app.route("/admin/coupons/<int:cid>/toggle", methods=["POST"])
@admin_required
def toggle_coupon(cid):
    with db() as con: con.execute("UPDATE coupons SET active=1-active WHERE id=?",(cid,))
    return redirect(url_for("admin_coupons"))

@app.route("/admin/settings", methods=["GET","POST"])
@admin_required
def admin_settings():
    if request.method=="POST":
        for key in ["store_name","store_phone","store_address","delivery_fee","free_delivery_threshold"]:
            val=request.form.get(key,"").strip()
            if key in ("delivery_fee","free_delivery_threshold"):
                try:
                    if float(val)<0: raise ValueError()
                except ValueError:
                    flash("Delivery amount শূন্য বা তার বেশি হতে হবে।","error"); return redirect(url_for("admin_settings"))
            set_setting(key,val)
        flash("Store settings সংরক্ষিত হয়েছে।","success"); return redirect(url_for("admin_settings"))
    return render_template("admin.html",section="settings",settings={k:setting(k) for k in ["store_name","store_phone","store_address","delivery_fee","free_delivery_threshold"]})

@app.route("/admin/password", methods=["POST"])
@admin_required
def change_password():
    old=request.form.get("old_password",""); new=request.form.get("new_password","")
    if not check_password_hash(setting("admin_hash"),old):
        flash("বর্তমান password সঠিক নয়।","error")
    elif len(new)<12:
        flash("নতুন password অন্তত ১২ অক্ষরের হতে হবে।","error")
    else:
        set_setting("admin_hash",generate_password_hash(new))
        flash("Admin password পরিবর্তন হয়েছে।","success")
    return redirect(url_for("admin_settings"))

@app.route("/admin/payments")
@admin_required
def admin_payments():
    return render_template("admin.html",section="payments")

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)
