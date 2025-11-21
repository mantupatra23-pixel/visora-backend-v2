# auth_payments.py
import os
import time
import uuid
import hmac
import json
import hashlib
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, current_app
from pymongo import MongoClient
from dotenv import load_dotenv
import bcrypt
import jwt
import razorpay
import stripe

load_dotenv()

bp = Blueprint("auth_payments", __name__)

# ---------------------------
# CONFIG (from .env)
# ---------------------------
MONGO_URI = os.getenv("MONGO_URI")
JWT_SECRET = os.getenv("JWT_SECRET", "change_this_long_secret")
JWT_ALGO = "HS256"
JWT_ACCESS_EXPIRES = int(os.getenv("JWT_ACCESS_EXPIRES", 900))   # 15 min
JWT_REFRESH_EXPIRES = int(os.getenv("JWT_REFRESH_EXPIRES", 60*60*24*30))  # 30 days

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

client = MongoClient(MONGO_URI)
db = client.get_default_database()
users_col = db.get_collection("users")
payments_col = db.get_collection("payments")

if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
else:
    rz_client = None

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY


# ---------------------------
# HELPERS
# ---------------------------
def hash_password(plain: str) -> bytes:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())

def check_password(plain: str, hashed: bytes) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed)

def create_access_token(user_id):
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_ACCESS_EXPIRES)).timestamp()),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def create_refresh_token(user_id):
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_REFRESH_EXPIRES)).timestamp()),
        "type": "refresh"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception as e:
        return None

def require_auth(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401
        token = auth.split(" ",1)[1]
        data = decode_token(token)
        if not data or data.get("type")!="access":
            return jsonify({"error": "Invalid token"}), 401
        user = users_col.find_one({"_id": data["sub"]})
        if not user:
            return jsonify({"error": "User not found"}), 404
        request.current_user = user
        return fn(*args, **kwargs)
    return wrapper

# ---------------------------
# AUTH ROUTES
# ---------------------------
@bp.route("/auth/register", methods=["POST"])
def register():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "")
    if not email or not password:
        return jsonify({"error":"email and password required"}), 400
    if users_col.find_one({"email": email}):
        return jsonify({"error":"User already exists"}), 400
    hashed = hash_password(password)
    user_doc = {
        "_id": str(uuid.uuid4())[:16],
        "email": email,
        "name": name,
        "password": hashed,
        "credits": 0,
        "is_admin": False,
        "created_at": datetime.utcnow()
    }
    users_col.insert_one(user_doc)
    access = create_access_token(user_doc["_id"])
    refresh = create_refresh_token(user_doc["_id"])
    return jsonify({"status":True, "access_token": access, "refresh_token": refresh, "user_id":user_doc["_id"]})

@bp.route("/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    user = users_col.find_one({"email": email})
    if not user or not check_password(password, user["password"]):
        return jsonify({"error":"Invalid credentials"}), 401
    access = create_access_token(user["_id"])
    refresh = create_refresh_token(user["_id"])
    return jsonify({"status":True, "access_token": access, "refresh_token": refresh, "credits": user.get("credits",0)})

@bp.route("/auth/refresh", methods=["POST"])
def refresh():
    data = request.json or {}
    token = data.get("refresh_token")
    payload = decode_token(token)
    if not payload or payload.get("type")!="refresh":
        return jsonify({"error":"Invalid refresh token"}), 401
    user_id = payload["sub"]
    user = users_col.find_one({"_id": user_id})
    if not user:
        return jsonify({"error":"User not found"}), 404
    access = create_access_token(user_id)
    return jsonify({"access_token": access})

# ---------------------------
# USER PROFILE / CREDIT CHECK
# ---------------------------
@bp.route("/me", methods=["GET"])
@require_auth
def me():
    user = request.current_user
    return jsonify({
        "email": user["email"],
        "name": user.get("name"),
        "credits": user.get("credits", 0),
        "is_admin": user.get("is_admin", False)
    })

# ---------------------------
# CREDIT USAGE (deduct credits for job)
# ---------------------------
def consume_credits(user_id, amount):
    # atomic update
    res = users_col.find_one_and_update(
        {"_id": user_id, "credits": {"$gte": amount}},
        {"$inc": {"credits": -amount}}
    )
    return res is not None

@bp.route("/consume", methods=["POST"])
@require_auth
def consume():
    data = request.json or {}
    amount = int(data.get("amount", 1))
    user = request.current_user
    ok = consume_credits(user["_id"], amount)
    if not ok:
        return jsonify({"status": False, "error": "Not enough credits"}), 402
    # optionally create a job record
    payments_col.insert_one({
        "type": "consume",
        "user_id": user["_id"],
        "amount": amount,
        "ts": datetime.utcnow()
    })
    return jsonify({"status": True, "remaining": users_col.find_one({"_id": user["_id"]})["credits"]})


# ---------------------------
# PAYMENT: RAZORPAY (India)
# ---------------------------
@bp.route("/pay/razorpay/create_order", methods=["POST"])
@require_auth
def razorpay_create_order():
    data = request.json or {}
    amount_in_rupees = float(data.get("amount_rupees", 99))  # amount in INR
    credits = int(data.get("credits", 100))
    if not rz_client:
        return jsonify({"error": "Razorpay not configured"}), 500

    amount_paise = int(amount_in_rupees * 100)
    receipt = f"rcpt_{uuid.uuid4().hex[:8]}"
    order = rz_client.order.create(dict(amount=amount_paise, currency="INR", receipt=receipt, payment_capture=1))
    # save pending payment
    payments_col.insert_one({
        "provider":"razorpay",
        "order_id": order["id"],
        "user_id": request.current_user["_id"],
        "amount_rupees": amount_in_rupees,
        "credits": credits,
        "status":"created",
        "created_at": datetime.utcnow()
    })
    return jsonify(order)

@bp.route("/webhook/razorpay", methods=["POST"])
def razorpay_webhook():
    # Razorpay webhook secret (set in dashboard) -> RAZORPAY_WEBHOOK_SECRET
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    payload = request.data
    signature = request.headers.get("X-Razorpay-Signature", "")
    if secret:
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return jsonify({"error":"invalid signature"}), 400
    event = request.json
    # handle payment.captured
    if event.get("event") == "payment.captured":
        payment = event["payload"]["payment"]["entity"]
        order_id = payment.get("order_id")
        # find pending order and mark complete
        doc = payments_col.find_one_and_update({"order_id": order_id}, {"$set":{"status":"paid","payment_id": payment["id"], "paid_at": datetime.utcnow()}}, return_document=True)
        if doc:
            # add credits to user
            users_col.update_one({"_id": doc["user_id"]}, {"$inc": {"credits": int(doc.get("credits",0))}})
    return jsonify({"ok": True})

# ---------------------------
# PAYMENT: STRIPE (global)
# ---------------------------
@bp.route("/pay/stripe/create_session", methods=["POST"])
@require_auth
def stripe_create_session():
    data = request.json or {}
    amount_usd = float(data.get("amount_usd", 1.99))
    credits = int(data.get("credits", 100))
    # create product/session on Stripe Checkout
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'unit_amount': int(amount_usd*100),
                'product_data': {'name': f'Visora Credits {credits}'},
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url=os.getenv("SUCCESS_URL", "https://your-site.com/success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=os.getenv("CANCEL_URL", "https://your-site.com/cancel")
    )
    # store payment intent
    payments_col.insert_one({
        "provider":"stripe",
        "session_id": session.id,
        "user_id": request.current_user["_id"],
        "amount_usd": amount_usd,
        "credits": credits,
        "status":"created",
        "created_at": datetime.utcnow()
    })
    return jsonify({"id": session.id, "url": session.url})

@bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = STRIPE_WEBHOOK_SECRET
    event = None
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    # handle checkout.session.completed
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        # find record
        doc = payments_col.find_one_and_update({"session_id": session["id"]}, {"$set":{"status":"paid","paid_at": datetime.utcnow()}}, return_document=True)
        if doc:
            users_col.update_one({"_id": doc["user_id"]}, {"$inc": {"credits": int(doc.get("credits",0))}})
    return jsonify({"ok": True})

# ---------------------------
# ADMIN ENDPOINTS
# ---------------------------
def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = getattr(request, "current_user", None)
        if not user or not user.get("is_admin"):
            return jsonify({"error":"admin only"}), 403
        return fn(*args, **kwargs)
    return wrapper

@bp.route("/admin/users", methods=["GET"])
@require_auth
@admin_required
def admin_list_users():
    q = users_col.find().sort("created_at", -1)
    out = []
    for u in q:
        out.append({
            "id": u["_id"],
            "email": u["email"],
            "name": u.get("name"),
            "credits": u.get("credits",0),
            "created_at": u.get("created_at")
        })
    return jsonify(out)

@bp.route("/admin/add_credits", methods=["POST"])
@require_auth
@admin_required
def admin_add_credits():
    data = request.json or {}
    user_id = data.get("user_id")
    amount = int(data.get("amount",0))
    if not user_id or amount<=0:
        return jsonify({"error":"invalid"}), 400
    users_col.update_one({"_id": user_id}, {"$inc": {"credits": amount}})
    payments_col.insert_one({"type":"admin_add","user_id": user_id, "amount": amount, "ts": datetime.utcnow()})
    return jsonify({"ok": True})

@bp.route("/admin/refunds", methods=["GET"])
@require_auth
@admin_required
def admin_list_refunds():
    q = payments_col.find({"status":"refund_requested"}).sort("created_at", -1)
    return jsonify([p for p in q])

@bp.route("/admin/mark_refunded", methods=["POST"])
@require_auth
@admin_required
def admin_mark_refunded():
    data = request.json or {}
    pay_id = data.get("payment_id")
    payments_col.update_one({"_id": pay_id}, {"$set": {"status":"refunded"}})
    return jsonify({"ok": True})

# ---------------------------
# UTIL: create admin user helper
# ---------------------------
@bp.route("/util/create_admin", methods=["POST"])
def util_create_admin():
    data = request.json or {}
    secret = os.getenv("ADMIN_CREATE_SECRET")
    if data.get("secret") != secret:
        return jsonify({"error":"forbidden"}), 403
    email = data.get("email")
    pwd = data.get("password")
    if users_col.find_one({"email": email}):
        return jsonify({"error":"exists"}), 400
    hashed = hash_password(pwd)
    uid = str(uuid.uuid4())[:16]
    users_col.insert_one({"_id": uid, "email": email, "password": hashed, "credits": 0, "is_admin": True, "created_at": datetime.utcnow()})
    return jsonify({"ok": True, "user_id": uid})
