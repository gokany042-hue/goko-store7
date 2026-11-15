import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from urllib.parse import quote
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import text

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-before-deploying")
db_url = os.environ.get("DATABASE_URL", "sqlite:///goko_store.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Devam etmek için giriş yapmalısın."
csrf = CSRFProtect(app)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_active(self):
        return self.is_active_account

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)
    image = db.Column(db.String(255), default="")
    category = db.Column(db.String(60), default="Oyun Ürünleri")
    stock = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    items_text = db.Column(db.Text, nullable=False)
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default="Beklemede")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="orders")

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapped

@app.context_processor
def inject_globals():
    return {"cart_count": sum(session.get("cart", {}).values())}

@app.route("/")
def index():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query = Product.query
    if search:
        query = query.filter(Product.name.ilike(f"%{search}%"))
    if category:
        query = query.filter_by(category=category)
    products = query.order_by(Product.created_at.desc()).all()
    categories = [row[0] for row in db.session.query(Product.category).distinct().order_by(Product.category).all()]
    return render_template("index.html", products=products, categories=categories, search=search, selected_category=category)

@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = db.get_or_404(Product, product_id)
    return render_template("product.html", product=product)

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(username) > 40:
            flash("Kullanıcı adı 3-40 karakter olmalı.", "error")
        elif len(password) < 8:
            flash("Şifre en az 8 karakter olmalı.", "error")
        elif User.query.filter_by(username=username).first():
            flash("Bu kullanıcı adı zaten alınmış.", "error")
        else:
            user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Hesabın oluşturuldu. Hoş geldin!", "success")
            return redirect(url_for("index"))
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active_account and check_password_hash(user.password_hash, password):
            login_user(user)
            flash("Giriş başarılı.", "success")
            return redirect(url_for("admin_dashboard" if user.is_admin else "index"))
        flash("Kullanıcı adı veya şifre hatalı ya da hesap devre dışı.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Çıkış yaptın.", "success")
    return redirect(url_for("index"))

@app.route("/cart")
def cart():
    cart_data = session.get("cart", {})
    products = []
    total = 0
    for pid, qty in cart_data.items():
        product = db.session.get(Product, int(pid))
        if product:
            subtotal = product.price * qty
            products.append({"product": product, "qty": qty, "subtotal": subtotal})
            total += subtotal
    return render_template("cart.html", items=products, total=total)

@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = db.get_or_404(Product, product_id)
    cart_data = session.get("cart", {})
    key = str(product_id)
    cart_data[key] = min(cart_data.get(key, 0) + 1, max(product.stock, 1))
    session["cart"] = cart_data
    flash("Ürün sepete eklendi.", "success")
    return redirect(request.referrer or url_for("index"))

@app.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart(product_id):
    product = db.get_or_404(Product, product_id)
    try:
        qty = int(request.form.get("quantity", "1"))
    except ValueError:
        qty = 1
    cart_data = session.get("cart", {})
    if qty <= 0 or product.stock <= 0:
        cart_data.pop(str(product_id), None)
    else:
        cart_data[str(product_id)] = min(qty, product.stock)
    session["cart"] = cart_data
    return redirect(url_for("cart"))

@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def remove_from_cart(product_id):
    cart_data = session.get("cart", {})
    cart_data.pop(str(product_id), None)
    session["cart"] = cart_data
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    cart_data = session.get("cart", {})
    if not cart_data:
        flash("Sepetin boş.", "error")
        return redirect(url_for("cart"))
    lines, total = [], 0
    for pid, qty in cart_data.items():
        product = db.session.get(Product, int(pid))
        if not product or qty > product.stock:
            flash("Bir ürünün stok durumu değişmiş. Sepetini kontrol et.", "error")
            return redirect(url_for("cart"))
        lines.append(f"{product.name} x {qty}")
        total += product.price * qty
    order = Order(user_id=current_user.id, items_text="\n".join(lines), total=total)
    db.session.add(order)
    for pid, qty in cart_data.items():
        product = db.session.get(Product, int(pid))
        product.stock -= qty
    db.session.commit()
    session["cart"] = {}
    flash("Siparişin oluşturuldu. Telegram üzerinden ödeme detaylarını gönderebilirsin.", "success")
    return redirect(url_for("order_detail", order_id=order.id))

@app.route("/order/<int:order_id>")
@login_required
def order_detail(order_id):
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    product_lines = order.items_text.replace("\\n", ", ")
    message = (
        f"Merhaba, GÖKØ STORE üzerinden #{order.id} numaralı siparişi oluşturdum.\\n"
        f"Kullanıcı: {order.user.username}\\n"
        f"Ürünler: {product_lines}\\n"
        f"Toplam: {order.total:.2f} TL\\n"
        "Ödeme yapmak istiyorum. Ödeme bilgilerini paylaşır mısınız?"
    )
    telegram_url = "https://t.me/Denktinee?text=" + quote(message)
    return render_template("order_detail.html", order=order, telegram_url=telegram_url)

@app.route("/my-orders")
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("my_orders.html", orders=orders)

@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin/dashboard.html",
        user_count=User.query.count(), product_count=Product.query.count(),
        order_count=Order.query.count(), orders=Order.query.order_by(Order.created_at.desc()).limit(8).all())

@app.route("/admin/products")
@admin_required
def admin_products():
    return render_template("admin/products.html", products=Product.query.order_by(Product.created_at.desc()).all())

@app.route("/admin/products/new", methods=["GET", "POST"])
@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def product_form(product_id=None):
    product = db.session.get(Product, product_id) if product_id else None
    if product_id and not product:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = float(request.form.get("price", "0"))
            stock = int(request.form.get("stock", "0"))
        except ValueError:
            flash("Fiyat ve stok sayısal olmalı.", "error")
            return render_template("admin/product_form.html", product=product)
        if not name or price < 0 or stock < 0:
            flash("Ürün adı, fiyat ve stok bilgilerini kontrol et.", "error")
            return render_template("admin/product_form.html", product=product)
        if not product:
            product = Product(name=name, price=price, stock=stock)
            db.session.add(product)
        product.name = name
        product.price = price
        product.stock = stock
        product.description = request.form.get("description", "").strip()
        product.category = request.form.get("category", "Oyun Ürünleri").strip()
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
                flash("Yalnızca PNG, JPG, JPEG veya WEBP görsel yükleyebilirsin.", "error")
                return render_template("admin/product_form.html", product=product)
            safe_name = f"{int(datetime.utcnow().timestamp())}_{filename}"
            image_file.save(os.path.join(app.config["UPLOAD_FOLDER"], safe_name))
            product.image = safe_name
        db.session.commit()
        flash("Ürün kaydedildi.", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_form.html", product=product)

@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@admin_required
def delete_product(product_id):
    product = db.get_or_404(Product, product_id)
    db.session.delete(product)
    db.session.commit()
    flash("Ürün silindi.", "success")
    return redirect(url_for("admin_products"))

@app.route("/admin/users")
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)

@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("Kendi hesabını buradan devre dışı bırakamazsın.", "error")
    elif user.is_admin:
        flash("Başka bir admin hesabını buradan değiştiremezsin.", "error")
    else:
        user.is_active_account = not user.is_active_account
        db.session.commit()
        flash("Kullanıcı durumu güncellendi.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("admin/orders.html", orders=orders)

@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order(order_id):
    order = db.get_or_404(Order, order_id)
    status = request.form.get("status")
    if status in ["Beklemede", "İşleniyor", "Tamamlandı", "İptal"]:
        order.status = status
        db.session.commit()
        flash("Sipariş durumu güncellendi.", "success")
    return redirect(url_for("admin_orders"))

@app.errorhandler(403)
def forbidden(_):
    return render_template("403.html"), 403

def init_app():
    with app.app_context():
        db.create_all()
        admin_name = os.environ.get("ADMIN_USERNAME")
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_name and admin_password and not User.query.filter_by(username=admin_name).first():
            admin = User(username=admin_name, password_hash=generate_password_hash(admin_password), is_admin=True)
            db.session.add(admin)
            db.session.commit()

init_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
