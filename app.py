import os
import requests
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tradetrackv-super-secret-key-2026")

database_url = os.environ.get("DATABASE_URL", "sqlite:///tradetrackv.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Shop(db.Model):
    __tablename__ = "shops"
    id = db.Column(db.Integer, primary_key=True)
    shop_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    owner_pin = db.Column(db.String(10), default="1234", nullable=False)
    plan_tier = db.Column(db.String(20), default="freemium", nullable=False)
    cloud_linked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    products = db.relationship("Product", backref="shop", lazy=True, cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", backref="shop", lazy=True, cascade="all, delete-orphan")


class GlobalCatalog(db.Model):
    __tablename__ = "global_catalog"
    barcode = db.Column(db.String(64), primary_key=True)
    product_name = db.Column(db.String(150), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    barcode = db.Column(db.String(64), nullable=True, index=True)
    name = db.Column(db.String(150), nullable=False)
    cost_price = db.Column(db.Float, default=0.0)
    selling_price = db.Column(db.Float, nullable=False)
    stock_qty = db.Column(db.Integer, default=0)
    bulk_barcode = db.Column(db.String(64), nullable=True, index=True)
    bulk_unit_name = db.Column(db.String(30), nullable=True)
    bulk_factor = db.Column(db.Integer, default=1)
    bulk_selling_price = db.Column(db.Float, nullable=True)
    bulk_cost_price = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, nullable=True)
    product_name = db.Column(db.String(150), nullable=False)
    tx_type = db.Column(db.String(20), nullable=False)
    qty = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, default=0.0)
    recorded_by = db.Column(db.String(30), default="Salesgirl")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


with app.app_context():
    db.create_all()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "shop_id" not in session:
            return jsonify({"error": "Unauthorized. Please log in."}), 401
        return f(*args, **kwargs)
    return decorated


def is_premium_shop(shop):
    return shop.plan_tier == "premium" and shop.cloud_linked


def seed_starter_items_to_db(shop_id):
    starters = [
        {"barcode": "615110001", "name": "Peak Evaporated Milk (Tin)", "cost": 700, "sell": 800, "qty": 24},
        {"barcode": "615110002", "name": "Golden Penny Semovita 2kg", "cost": 2500, "sell": 2800, "qty": 15},
        {"barcode": "615110003", "name": "Indomie Chicken Carton (40pcs)", "cost": 9600, "sell": 10500, "qty": 8},
        {"barcode": "615110004", "name": "Devon Kings Cooking Oil 1L", "cost": 2200, "sell": 2500, "qty": 12},
    ]
    for item in starters:
        db.session.add(Product(
            shop_id=shop_id,
            barcode=item["barcode"],
            name=item["name"],
            cost_price=item["cost"],
            selling_price=item["sell"],
            stock_qty=item["qty"]
        ))
        if not db.session.get(GlobalCatalog, item["barcode"]):
            db.session.add(GlobalCatalog(barcode=item["barcode"], product_name=item["name"]))
    db.session.commit()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}
    shop_name = (data.get("shop_name") or "").strip()
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    owner_pin = (data.get("owner_pin") or "1234").strip()
    plan_tier = "premium" if data.get("plan_tier") == "premium" else "freemium"
    load_starters = data.get("load_starters", True)

    if not shop_name or not username or not password:
        return jsonify({"error": "Shop name, username/phone, and password are required."}), 400

    if Shop.query.filter_by(username=username).first():
        return jsonify({"error": "That username or phone number is already registered."}), 400

    shop = Shop(
        shop_name=shop_name,
        username=username,
        password_hash=generate_password_hash(password),
        owner_pin=owner_pin if owner_pin.isdigit() and len(owner_pin) == 4 else "1234",
        plan_tier=plan_tier,
        cloud_linked=(plan_tier == "premium")
    )
    db.session.add(shop)
    db.session.commit()

    if plan_tier == "premium" and load_starters:
        seed_starter_items_to_db(shop.id)

    session["shop_id"] = shop.id
    return jsonify({
        "message": "Shop registered!",
        "shop": {
            "id": shop.id,
            "shop_name": shop.shop_name,
            "username": shop.username,
            "plan_tier": shop.plan_tier,
            "cloud_linked": shop.cloud_linked
        }
    })


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    shop = Shop.query.filter_by(username=username).first()
    if not shop or not check_password_hash(shop.password_hash, password):
        return jsonify({"error": "Invalid username/phone or password."}), 401

    session["shop_id"] = shop.id
    return jsonify({
        "message": "Logged in",
        "shop": {
            "id": shop.id,
            "shop_name": shop.shop_name,
            "username": shop.username,
            "plan_tier": shop.plan_tier,
            "cloud_linked": shop.cloud_linked
        }
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/verify-pin", methods=["POST"])
@login_required
def verify_pin():
    shop = db.session.get(Shop, session["shop_id"])
    pin = (request.json or {}).get("pin", "")
    if pin == shop.owner_pin:
        return jsonify({"valid": True})
    return jsonify({"valid": False, "error": "Incorrect Owner PIN"}), 403


@app.route("/api/state", methods=["GET"])
@login_required
def get_state():
    shop = db.session.get(Shop, session["shop_id"])

    if not is_premium_shop(shop):
        return jsonify({
            "shop": {
                "id": shop.id,
                "shop_name": shop.shop_name,
                "username": shop.username,
                "plan_tier": "freemium",
                "cloud_linked": False
            },
            "db_storage_enabled": False,
            "products": [],
            "transactions": []
        })

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    products = Product.query.filter_by(shop_id=shop.id).order_by(Product.name.asc()).all()
    tx_query = Transaction.query.filter_by(shop_id=shop.id)
    if start_date:
        tx_query = tx_query.filter(Transaction.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        tx_query = tx_query.filter(Transaction.created_at <= datetime.fromisoformat(end_date + "T23:59:59"))

    transactions = tx_query.order_by(Transaction.created_at.desc()).limit(300).all()

    return jsonify({
        "shop": {
            "id": shop.id,
            "shop_name": shop.shop_name,
            "username": shop.username,
            "plan_tier": "premium",
            "cloud_linked": True
        },
        "db_storage_enabled": True,
        "products": [
            {
                "id": p.id,
                "barcode": p.barcode or "",
                "name": p.name,
                "cost_price": p.cost_price,
                "selling_price": p.selling_price,
                "stock_qty": p.stock_qty,
            }
            for p in products
        ],
        "transactions": [
            {
                "id": t.id,
                "product_name": t.product_name,
                "tx_type": t.tx_type,
                "qty": t.qty,
                "total_amount": t.total_amount,
                "profit": t.profit,
                "recorded_by": t.recorded_by,
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for t in transactions
        ]
    })


@app.route("/api/barcode-lookup/<barcode>", methods=["GET"])
@login_required
def barcode_lookup(barcode):
    shop = db.session.get(Shop, session["shop_id"])
    clean_code = barcode.strip()

    if is_premium_shop(shop):
        local_prod = Product.query.filter_by(shop_id=shop.id, barcode=clean_code).first()
        if local_prod:
            return jsonify({
                "source": "shop_db",
                "found": True,
                "product": {
                    "id": local_prod.id,
                    "barcode": local_prod.barcode,
                    "name": local_prod.name,
                    "selling_price": local_prod.selling_price,
                    "stock_qty": local_prod.stock_qty,
                }
            })

    global_item = db.session.get(GlobalCatalog, clean_code)
    if global_item:
        return jsonify({
            "source": "render_global_catalog",
            "found": True,
            "name": global_item.product_name,
            "barcode": clean_code
        })

    open_sources = [
        ("open_food_facts", f"https://world.openfoodfacts.org/api/v2/product/{clean_code}.json"),
        ("open_beauty_facts", f"https://world.openbeautyfacts.org/api/v2/product/{clean_code}.json"),
        ("open_products_facts", f"https://world.openproductsfacts.org/api/v2/product/{clean_code}.json"),
    ]

    for source_name, url in open_sources:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "TradetrackV/1.0 (contact@tradetrackv.app)"},
                timeout=4
            )
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("status") == 1:
                    prod_data = payload.get("product", {})
                    name = (
                        prod_data.get("product_name")
                        or prod_data.get("product_name_en")
                        or prod_data.get("generic_name")
                    )
                    brand = (prod_data.get("brands") or "").split(",")[0].strip()
                    if name:
                        name = name.strip()
                        full_name = f"{brand} {name}".strip() if brand and brand.lower() not in name.lower() else name
                        clipped_name = full_name[:150]
                        existing = db.session.get(GlobalCatalog, clean_code)
                        if not existing:
                            db.session.add(GlobalCatalog(barcode=clean_code, product_name=clipped_name))
                            db.session.commit()
                        return jsonify({
                            "source": source_name,
                            "found": True,
                            "name": clipped_name,
                            "barcode": clean_code
                        })
        except Exception:
            pass

    return jsonify({"found": False, "barcode": clean_code})

@app.route("/api/link-barcode", methods=["POST"])
@login_required
def link_barcode():
    """Links a barcode to a product name in Render's GlobalCatalog DB."""
    data = request.json or {}
    barcode = (data.get("barcode") or "").strip()
    product_name = (data.get("product_name") or "").strip()
    if not barcode or not product_name:
        return jsonify({"error": "Barcode and product name are required."}), 400

    existing = db.session.get(GlobalCatalog, barcode)
    if existing:
        existing.product_name = product_name[:150]
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.session.add(GlobalCatalog(barcode=barcode, product_name=product_name[:150]))
    db.session.commit()
    return jsonify({"message": "Barcode linked to Render database"})


@app.route("/api/products", methods=["POST"])
@login_required
def save_product():
    shop = db.session.get(Shop, session["shop_id"])
    if not is_premium_shop(shop):
        return jsonify({
            "error": "Freemium records stay on your device only. Pay ₦4,000 to Moniepoint 09061693582 and WhatsApp 09061693582 for cloud DB storage."
        }), 403

    data = request.json or {}
    prod_id = data.get("id")
    barcode = (data.get("barcode") or "").strip()
    name = (data.get("name") or "").strip()
    cost_price = float(data.get("cost_price") or 0)
    selling_price = float(data.get("selling_price") or 0)
    stock_qty = int(data.get("stock_qty") or 0)
    bulk_barcode = (data.get("bulk_barcode") or "").strip() or None
    bulk_unit_name = (data.get("bulk_unit_name") or "").strip() or None
    bulk_factor = max(1, int(data.get("bulk_factor") or 1))
    raw_bulk_sell = data.get("bulk_selling_price")
    bulk_selling_price = float(raw_bulk_sell) if raw_bulk_sell not in (None, "") else None
    raw_bulk_cost = data.get("bulk_cost_price")
    bulk_cost_price = float(raw_bulk_cost) if raw_bulk_cost not in (None, "") else None

    if not name or selling_price <= 0:
        return jsonify({"error": "Product name and selling price are required."}), 400

    if prod_id:
        prod = Product.query.filter_by(id=prod_id, shop_id=shop.id).first()
        if not prod:
            return jsonify({"error": "Product not found."}), 404
        prod.barcode = barcode
        prod.name = name
        prod.cost_price = cost_price
        prod.selling_price = selling_price
        prod.stock_qty = stock_qty
        prod.bulk_barcode = bulk_barcode
        prod.bulk_unit_name = bulk_unit_name
        prod.bulk_factor = bulk_factor
        prod.bulk_selling_price = bulk_selling_price
        prod.bulk_cost_price = bulk_cost_price

    else:
        prod = Product(
            shop_id=shop.id,
            barcode=barcode,
            name=name,
            cost_price=cost_price,
            selling_price=selling_price,
            stock_qty=stock_qty,
            bulk_barcode=bulk_barcode,
            bulk_unit_name=bulk_unit_name,
            bulk_factor=bulk_factor,
            bulk_selling_price=bulk_selling_price,
            bulk_cost_price=bulk_cost_price,

        )
        db.session.add(prod)

    if barcode and name:
        existing_global = db.session.get(GlobalCatalog, barcode)
        if existing_global:
            existing_global.product_name = name[:150]
        else:
            db.session.add(GlobalCatalog(barcode=barcode, product_name=name[:150]))

    db.session.commit()
    return jsonify({"message": "Saved & barcode linked to Cloud DB"})


@app.route("/api/transactions", methods=["POST"])
@login_required
def record_transaction():
    shop = db.session.get(Shop, session["shop_id"])
    if not is_premium_shop(shop):
        return jsonify({
            "error": "Freemium sales stay in local device storage only and are not written to the database."
        }), 403

    data = request.json or {}
    product_id = data.get("product_id")
    tx_type = data.get("tx_type", "SALE")
    qty = int(data.get("qty") or 1)
    recorded_by = data.get("recorded_by", "Salesgirl")
    unit_mode = (data.get("unit_mode") or "single").strip().lower()

    prod = Product.query.filter_by(id=product_id, shop_id=shop.id).first()
    if not prod:
        return jsonify({"error": "Product not found in shop inventory."}), 404


    if unit_mode == "bulk":
        factor = max(1, int(prod.bulk_factor or 1))
        base_qty_needed = qty * factor
        eff_selling_price = (
            float(prod.bulk_selling_price)
            if prod.bulk_selling_price is not None
            else float(prod.selling_price) * factor
        )
        eff_cost_price = (
            float(prod.bulk_cost_price)
            if prod.bulk_cost_price is not None
            else float(prod.cost_price) * factor
        )
    else:
        base_qty_needed = qty
        eff_selling_price = float(prod.selling_price)
        eff_cost_price = float(prod.cost_price)

    if prod.stock_qty < base_qty_needed:
        return jsonify({"error": f"Insufficient stock! Only {prod.stock_qty} single unit(s) left."}), 400

    prod.stock_qty -= base_qty_needed
    total_amount = (eff_cost_price * qty) if tx_type == "FAMILY_DRAW" else (eff_selling_price * qty)
    profit = 0.0 if tx_type == "FAMILY_DRAW" else ((eff_selling_price - eff_cost_price) * qty)

    db.session.add(Transaction(
        shop_id=shop.id,
        product_id=prod.id,
        product_name=prod.name,
        tx_type=tx_type,
        qty=qty,
        unit_price=eff_selling_price,
        unit_cost=eff_cost_price,
        total_amount=total_amount,
        profit=profit,
        recorded_by=recorded_by
    ))
    db.session.commit()
    return jsonify({"message": "Recorded in Cloud DB"})
@app.route("/api/shop/<int:shop_id>/barcode-lookup", methods=["GET"])
@login_required
def shop_unit_barcode_lookup(shop_id):
    if session.get("shop_id") != shop_id:
        return jsonify({"error": "Forbidden: wrong shop."}), 403

    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"error": "Barcode value is required."}), 400

    shop = db.session.get(Shop, shop_id)
    if not shop:
        return jsonify({"error": "Shop not found."}), 404

    prod = Product.query.filter_by(shop_id=shop_id, barcode=code).first()
    matched_unit = "single"
    factor = 1

    if not prod:
        prod = Product.query.filter_by(shop_id=shop_id, bulk_barcode=code).first()
        if prod:
            matched_unit = "bulk"
            factor = int(prod.bulk_factor or 1)

    if not prod:
        return jsonify({"found": False, "error": "Barcode not found in this shop."}), 404

    base_stock = int(prod.stock_qty or 0)
    factor = max(1, int(factor or 1))
    available_in_unit = (base_stock // factor) if base_stock > 0 else 0

    if matched_unit == "bulk":
        unit_price = (
            float(prod.bulk_selling_price)
            if prod.bulk_selling_price is not None
            else float(prod.selling_price) * factor
        )
        unit_cost = (
            float(prod.bulk_cost_price)
            if prod.bulk_cost_price is not None
            else float(prod.cost_price) * factor
        )
        unit_label = prod.bulk_unit_name or "bulk"
    else:
        unit_price = float(prod.selling_price)
        unit_cost = float(prod.cost_price)
        unit_label = "single"

    human_stock = f"{base_stock} unit(s)"
    if prod.bulk_unit_name and (prod.bulk_factor or 1) > 1:
        b_factor = int(prod.bulk_factor)
        full_bulks, loose_units = divmod(base_stock, b_factor)
        parts = []
        if full_bulks > 0:
            parts.append(f"{full_bulks} {prod.bulk_unit_name}(s)")
        if loose_units > 0 or not parts:
            parts.append(f"{loose_units} single(s)")
        human_stock = ", ".join(parts)

    return jsonify({
        "found": True,
        "matched_unit": matched_unit,
        "unit_label": unit_label,
        "factor_to_base": factor,
        "available_in_unit": available_in_unit,
        "human_stock": human_stock,
        "product": {
            "id": prod.id,
            "name": prod.name,
            "barcode": prod.barcode,
            "bulk_barcode": prod.bulk_barcode,
            "selling_price": unit_price,
            "cost_price": unit_cost,
            "stock_qty": base_stock,
        },
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
