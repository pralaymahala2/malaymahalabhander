import os, sqlite3, secrets, hmac, hashlib
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "store.db")
UPLOAD_DIR = os.path.join(BASE, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("STORE_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, price REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0, image TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS coupons (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, discount_percent INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, items_json TEXT NOT NULL, total REAL NOT NULL, coupon TEXT NOT NULL DEFAULT '', payment_method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'New', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    defaults={"store_name":"Pralay Grocery Store","delivery_fee":"30","currency":"INR"}
    for k,v in defaults.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    if not con.execute("SELECT id FROM admins LIMIT 1").fetchone():
        con.execute("INSERT INTO admins(username,password_hash) VALUES(?,?)",("admin",generate_password_hash("ChangeMe123!")))
    if not con.execute("SELECT id FROM products LIMIT 1").fetchone():
        con.executemany("INSERT INTO products(name,category,price,stock,image,description) VALUES(?,?,?,?,?,?)",[
            ("Rice 1 kg","Staples",65,30,"","Sample product — edit this in admin"),
            ("Mustard Oil 1 L","Oil",155,15,"","Sample product — edit this in admin"),
            ("Potato 1 kg","Vegetables",30,50,"","Sample product — edit this in admin"),
            ("Tea 250 g","Beverages",90,20,"","Sample product — edit this in admin")])
    con.commit(); con.close()

def setting(key, default=""):
    con=db(); row=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); con.close()
    return row["value"] if row else default

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped

@app.context_processor
def inject_settings():
    return {"store_name":setting("store_name","Pralay Grocery Store")}

@app.route("/")
def home():
    con=db()
    products=con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    con.close()
    return render_template("index.html", products=products, delivery_fee=float(setting("delivery_fee","30")))

@app.route("/api/coupon", methods=["POST"])
def check_coupon():
    data=request.get_json(silent=True) or {}
    code=(data.get("code") or "").strip().upper()
    con=db(); row=con.execute("SELECT * FROM coupons WHERE code=? AND active=1",(code,)).fetchone(); con.close()
    if not row: return jsonify(ok=False, message="Coupon code is invalid or inactive.")
    return jsonify(ok=True, code=row["code"], discount_percent=row["discount_percent"])

@app.route("/checkout", methods=["POST"])
def checkout():
    import json
    data=request.get_json(silent=True) or {}
    customer=(data.get("customer") or "").strip()
    phone=(data.get("phone") or "").strip()
    address=(data.get("address") or "").strip()
    items=data.get("items") or []
    coupon=(data.get("coupon") or "").strip().upper()
    method=(data.get("payment_method") or "COD").upper()
    if not customer or not phone or not address or not items:
        return jsonify(ok=False,message="Please enter your name, phone, address and add products."),400
    if method not in {"COD","ONLINE"}:
        return jsonify(ok=False,message="Unsupported payment method."),400
    con=db(); total=0; clean=[]
    for item in items:
        try: pid=int(item.get("id")); qty=max(1,min(99,int(item.get("qty",1))))
        except (TypeError,ValueError): continue
        product=con.execute("SELECT id,name,price,stock FROM products WHERE id=?",(pid,)).fetchone()
        if not product or product["stock"] < qty:
            con.close(); return jsonify(ok=False,message="A product is unavailable or has insufficient stock. Refresh and try again."),400
        total += float(product["price"])*qty
        clean.append({"id":pid,"name":product["name"],"price":float(product["price"]),"qty":qty})
    if not clean:
        con.close(); return jsonify(ok=False,message="Your cart is empty."),400
    if coupon:
        c=con.execute("SELECT * FROM coupons WHERE code=? AND active=1",(coupon,)).fetchone()
        if not c:
            con.close(); return jsonify(ok=False,message="Coupon is invalid or inactive."),400
        total -= total*int(c["discount_percent"])/100
    total += float(setting("delivery_fee","30"))
    # Online payment intentionally requires gateway credentials and server-side verification.
    if method=="ONLINE":
        con.close()
        return jsonify(ok=False,message="Online payment is not activated yet. Add verified Razorpay credentials and server-side payment verification first, or choose Cash on Delivery."),400
    for item in clean:
        con.execute("UPDATE products SET stock=stock-? WHERE id=?",(item["qty"],item["id"]))
    cur=con.execute("INSERT INTO orders(customer,phone,address,items_json,total,coupon,payment_method) VALUES(?,?,?,?,?,?,?)",
        (customer,phone,address,json.dumps(clean),round(total,2),coupon,method))
    order_id=cur.lastrowid
    con.commit(); con.close()
    return jsonify(ok=True,message=f"Order #{order_id} placed successfully! Payment: Cash on Delivery.",order_id=order_id)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        con=db(); admin=con.execute("SELECT * FROM admins WHERE username=?",(username,)).fetchone(); con.close()
        if admin and check_password_hash(admin["password_hash"],password):
            session.clear(); session["admin_id"]=admin["id"]
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.")
    return render_template("login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def dashboard():
    con=db()
    products=con.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    coupons=con.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    orders=con.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    settings={r["key"]:r["value"] for r in con.execute("SELECT * FROM settings").fetchall()}
    con.close()
    return render_template("admin.html",products=products,coupons=coupons,orders=orders,settings=settings)

@app.route("/admin/product/save", methods=["POST"])
@admin_required
def save_product():
    pid=request.form.get("id","").strip()
    name=request.form.get("name","").strip()
    category=request.form.get("category","General").strip() or "General"
    description=request.form.get("description","").strip()
    try:
        price=float(request.form.get("price","0")); stock=int(request.form.get("stock","0"))
        if price<0 or stock<0 or not name: raise ValueError()
    except ValueError:
        flash("Enter a product name, non-negative price and stock."); return redirect(url_for("dashboard"))
    image=request.form.get("old_image","")
    f=request.files.get("image")
    if f and f.filename:
        filename=secure_filename(f.filename)
        ext=filename.rsplit(".",1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED:
            flash("Image must be PNG, JPG, JPEG, WEBP or GIF."); return redirect(url_for("dashboard"))
        filename=secrets.token_hex(8)+"."+ext
        f.save(os.path.join(UPLOAD_DIR,filename)); image="uploads/"+filename
    con=db()
    if pid:
        con.execute("UPDATE products SET name=?,category=?,price=?,stock=?,image=?,description=? WHERE id=?",(name,category,price,stock,image,description,pid))
    else:
        con.execute("INSERT INTO products(name,category,price,stock,image,description) VALUES(?,?,?,?,?,?)",(name,category,price,stock,image,description))
    con.commit(); con.close(); flash("Product saved."); return redirect(url_for("dashboard"))

@app.route("/admin/product/delete/<int:pid>", methods=["POST"])
@admin_required
def delete_product(pid):
    con=db(); con.execute("DELETE FROM products WHERE id=?",(pid,)); con.commit(); con.close()
    flash("Product deleted."); return redirect(url_for("dashboard"))

@app.route("/admin/coupon/save", methods=["POST"])
@admin_required
def save_coupon():
    code=request.form.get("code","").strip().upper()
    try: discount=int(request.form.get("discount_percent","0"))
    except ValueError: discount=0
    if not code or not 1<=discount<=90:
        flash("Enter a coupon code and discount from 1% to 90%."); return redirect(url_for("dashboard"))
    con=db()
    try:
        con.execute("INSERT INTO coupons(code,discount_percent,active) VALUES(?,?,1)",(code,discount)); con.commit()
        flash("Coupon added.")
    except sqlite3.IntegrityError: flash("That coupon code already exists.")
    con.close(); return redirect(url_for("dashboard"))

@app.route("/admin/coupon/toggle/<int:cid>", methods=["POST"])
@admin_required
def toggle_coupon(cid):
    con=db(); con.execute("UPDATE coupons SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(cid,)); con.commit(); con.close()
    return redirect(url_for("dashboard"))

@app.route("/admin/settings", methods=["POST"])
@admin_required
def save_settings():
    store_name=request.form.get("store_name","Pralay Grocery Store").strip() or "Pralay Grocery Store"
    try: delivery_fee=max(0,float(request.form.get("delivery_fee","0")))
    except ValueError: delivery_fee=0
    con=db()
    con.execute("INSERT INTO settings(key,value) VALUES('store_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(store_name,))
    con.execute("INSERT INTO settings(key,value) VALUES('delivery_fee',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(delivery_fee),))
    con.commit(); con.close(); flash("Store settings saved."); return redirect(url_for("dashboard"))

@app.route("/admin/password", methods=["POST"])
@admin_required
def change_password():
    current=request.form.get("current_password","")
    new=request.form.get("new_password","")
    if len(new)<12:
        flash("New password must be at least 12 characters."); return redirect(url_for("dashboard"))
    con=db(); admin=con.execute("SELECT * FROM admins WHERE id=?",(session["admin_id"],)).fetchone()
    if not admin or not check_password_hash(admin["password_hash"],current):
        con.close(); flash("Current password is incorrect."); return redirect(url_for("dashboard"))
    con.execute("UPDATE admins SET password_hash=? WHERE id=?",(generate_password_hash(new),session["admin_id"]))
    con.commit(); con.close(); flash("Admin password changed."); return redirect(url_for("dashboard"))

@app.route("/admin/orders/status/<int:oid>", methods=["POST"])
@admin_required
def order_status(oid):
    status=request.form.get("status","New")
    if status not in {"New","Confirmed","Packed","Out for delivery","Delivered","Cancelled"}: status="New"
    con=db(); con.execute("UPDATE orders SET status=? WHERE id=?",(status,oid)); con.commit(); con.close()
    return redirect(url_for("dashboard"))

init_db()
if __name__=="__main__":
    # Local testing only. Use a production WSGI server and HTTPS for a live store.
    app.run(host="127.0.0.1",port=5000,debug=False)
