import os, json, secrets, string, datetime, functools
from io import BytesIO
from urllib.parse import quote, urlparse
import re
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort, send_from_directory
from flask_socketio import SocketIO, join_room, emit
import firebase_admin
from firebase_admin import credentials, auth, db
from werkzeug.utils import secure_filename
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "CHANGE_ME_TO_A_LONG_RANDOM_SECRET")
socketio = SocketIO(app, cors_allowed_origins=[], async_mode="threading")

FIREBASE_DB_URL = os.environ.get("FIREBASE_DATABASE_URL", "")
FIREBASE_BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "")
UPI_ID = os.environ.get("UPI_ID", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "ayush_hackz")
CHANNEL_URL = os.environ.get("TELEGRAM_CHANNEL_URL", "https://t.me/Ayush_Hackz")
OWNER_URL = os.environ.get("TELEGRAM_OWNER_URL", "https://t.me/GOD_AYUSH")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "5"))
MAX_LINKS_PER_USER = int(os.environ.get("MAX_LINKS_PER_USER", "50"))
RESERVED_SLUGS = {"login", "logout", "register", "forgot", "dashboard", "owner", "store", "buy", "redeem", "links", "share", "static", "api"}
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}

def init_firebase():
    if firebase_admin._apps:
        return
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if raw:
        cred = credentials.Certificate(json.loads(raw))
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        cred = credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    else:
        raise RuntimeError("Set FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS")
    opts = {"databaseURL": FIREBASE_DB_URL}
    if FIREBASE_BUCKET:
        opts["storageBucket"] = FIREBASE_BUCKET
    firebase_admin.initialize_app(cred, opts)

init_firebase()

def root():
    return db.reference("/")

def profile(uid):
    return db.reference(f"users/{uid}")

def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    try:
        p = profile(uid).get() or {}
        return {"uid": uid, **p}
    except Exception:
        return None

def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            session.clear()
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def owner_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or not u.get("is_owner"):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def new_code():
    alphabet = string.ascii_uppercase + string.digits
    return "AYUSH-" + "".join(secrets.choice(alphabet) for _ in range(10))

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

@app.context_processor
def inject_globals():
    return {"site_name": "HACKPHONE", "channel_url": CHANNEL_URL, "owner_url": OWNER_URL, "current": current_user()}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip().lstrip("@")
        password = request.form.get("password", "")
        if not email or not username or len(password) < 8:
            flash("Valid email, username aur minimum 8-character password required.", "error")
            return redirect(url_for("register"))
        try:
            user = auth.create_user(email=email, password=password, display_name=username, email_verified=False)
            profile(user.uid).set({
                "email": email, "username": username, "credits": 0, "plan": "Normal",
                "plan_expires": None, "is_owner": False, "created_at": now_iso()
            })
            # Firebase password-reset/verification email delivery requires configured SMTP in this app.
            send_verification_email(email, user.uid)
            flash("Account ban gaya. Email verification link check karo, phir login karo.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Registration nahi ho paya: {str(e)[:180]}", "error")
    return render_template("auth.html", mode="register")

def send_mail(to_email, subject, body):
    import smtplib
    from email.message import EmailMessage
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True

def action_link(link, title):
    return f"{title}\n\n{link}\n\nAgar aapne request nahi ki thi, is email ko ignore karein."

def send_verification_email(email, uid):
    try:
        link = auth.generate_email_verification_link(email)
        send_mail(email, "Verify your HACKPHONE email", action_link(link, "Email verify karne ke liye link open karein."))
    except Exception:
        pass

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        # Firebase Admin SDK does not verify passwords. Use Firebase Auth REST API via Identity Toolkit.
        import requests
        api_key = os.environ.get("FIREBASE_WEB_API_KEY", "")
        if not api_key:
            flash("Owner ko FIREBASE_WEB_API_KEY configure karna hoga.", "error")
            return redirect(url_for("login"))
        try:
            r = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
                json={"email": email, "password": password, "returnSecureToken": True}, timeout=15
            )
            data = r.json()
            if r.status_code != 200:
                raise ValueError(data.get("error", {}).get("message", "Login failed"))
            decoded = auth.verify_id_token(data["idToken"])
            if not decoded.get("email_verified", False):
                raise ValueError("Pehle apna email verification link open karo.")
            p = profile(decoded["uid"]).get() or {}
            if not p:
                raise ValueError("User profile missing")
            session.clear()
            session["uid"] = decoded["uid"]
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"Login failed: {str(e)[:150]}", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        try:
            link = auth.generate_password_reset_link(email)
            sent = send_mail(email, "HACKPHONE password reset", action_link(link, "Password reset karne ke liye link open karein."))
            if not sent:
                flash("SMTP configured nahi hai. Owner SMTP settings add kare; reset link email nahi bheja gaya.", "error")
            else:
                flash("Agar account exist karta hai, reset email bhej diya gaya hai.", "success")
        except Exception:
            flash("Agar account exist karta hai, reset email bhej diya gaya hai.", "success")
        return redirect(url_for("login"))
    return render_template("auth.html", mode="forgot")

@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    tx = db.reference(f"transactions/{u['uid']}").get() or {}
    links = db.reference(f"custom_links/{u['uid']}").get() or {}
    return render_template("dashboard.html", user=u, transactions=list(tx.values())[-8:], link_count=len(links))

@app.route("/owner")
@login_required
@owner_required
def owner():
    users = db.reference("users").get() or {}
    plans = db.reference("plans").get() or {}
    payments = db.reference("payments").get() or {}
    codes = db.reference("redeem_codes").get() or {}
    return render_template("owner.html", users=users, plans=plans, payments=payments, codes=codes)

@app.route("/owner/credits", methods=["POST"])
@login_required
@owner_required
def owner_credits():
    uid = request.form.get("uid", "")
    amount = int(request.form.get("amount", "0"))
    action = request.form.get("action", "add")
    if amount <= 0 or action not in {"add", "deduct"}:
        flash("Valid amount choose karo.", "error")
        return redirect(url_for("owner"))
    ref = profile(uid)
    u = ref.get()
    if not u:
        flash("User nahi mila.", "error")
        return redirect(url_for("owner"))
    delta = amount if action == "add" else -amount
    if int(u.get("credits", 0)) + delta < 0:
        flash("Credits zero se kam nahi ho sakte.", "error")
        return redirect(url_for("owner"))
    ref.update({"credits": int(u.get("credits", 0)) + delta})
    log_tx(uid, "credit_adjustment", delta, f"Owner {action}")
    flash("Credits update ho gaye.", "success")
    return redirect(url_for("owner"))

@app.route("/owner/bulk-credits", methods=["POST"])
@login_required
@owner_required
def bulk_credits():
    amount = int(request.form.get("amount", "0"))
    action = request.form.get("action", "add")
    if amount <= 0 or action not in {"add", "deduct"}:
        flash("Invalid bulk adjustment.", "error")
        return redirect(url_for("owner"))
    users = db.reference("users").get() or {}
    for uid, u in users.items():
        if u.get("is_owner"):
            continue
        delta = amount if action == "add" else -amount
        new_value = max(0, int(u.get("credits", 0)) + delta)
        profile(uid).update({"credits": new_value})
        log_tx(uid, "bulk_adjustment", new_value-int(u.get("credits", 0)), f"Owner bulk {action}")
    flash("Bulk credits update complete.", "success")
    return redirect(url_for("owner"))

@app.route("/owner/plans", methods=["POST"])
@login_required
@owner_required
def save_plan():
    name = request.form.get("name", "").strip()
    plan_type = request.form.get("plan_type", "Normal")
    price = float(request.form.get("price", "0"))
    credits = int(request.form.get("credits", "0"))
    days = int(request.form.get("days", "0"))
    if not name or plan_type not in {"Normal", "VIP"} or price < 0 or credits < 0 or days < 0:
        flash("Plan details invalid.", "error")
        return redirect(url_for("owner"))
    key = secrets.token_hex(6)
    db.reference(f"plans/{key}").set({"name": name, "type": plan_type, "price": price, "credits": credits, "days": days, "active": True})
    flash("Plan create ho gaya.", "success")
    return redirect(url_for("owner"))

@app.route("/owner/plans/<plan_id>/delete", methods=["POST"])
@login_required
@owner_required
def delete_plan(plan_id):
    db.reference(f"plans/{plan_id}").delete()
    flash("Plan delete ho gaya.", "success")
    return redirect(url_for("owner"))

@app.route("/owner/redeem", methods=["POST"])
@login_required
@owner_required
def create_redeem():
    count = int(request.form.get("count", "1"))
    uses = int(request.form.get("uses", "1"))
    days = int(request.form.get("days", "0"))
    credits = int(request.form.get("credits", "0"))
    plan = request.form.get("plan", "Normal")
    if not (1 <= count <= 100 and 1 <= uses <= 10000 and 0 <= days <= 3650 and 0 <= credits <= 1000000 and plan in {"Normal", "VIP"}):
        flash("Redeem settings invalid.", "error")
        return redirect(url_for("owner"))
    for _ in range(count):
        code = new_code()
        db.reference(f"redeem_codes/{code}").set({"plan": plan, "days": days, "credits": credits, "max_uses": uses, "used": 0, "active": True, "created_at": now_iso()})
    flash(f"{count} redeem code(s) generate ho gaye.", "success")
    return redirect(url_for("owner"))

@app.route("/redeem", methods=["GET", "POST"])
@login_required
def redeem():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        ref = db.reference(f"redeem_codes/{code}")
        data = ref.get()
        if not data or not data.get("active") or int(data.get("used", 0)) >= int(data.get("max_uses", 1)):
            flash("Redeem code invalid, expired ya fully claimed hai.", "error")
            return redirect(url_for("redeem"))
        uid = session["uid"]
        claim_ref = db.reference(f"redeem_claims/{code}/{uid}")
        if claim_ref.get():
            flash("Tum ye code pehle claim kar chuke ho.", "error")
            return redirect(url_for("redeem"))
        uref = profile(uid)
        u = uref.get() or {}
        credits = int(data.get("credits", 0))
        plan = data.get("plan", "Normal")
        days = int(data.get("days", 0))
        expiry = None
        if days:
            expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat()
        uref.update({"credits": int(u.get("credits", 0)) + credits, "plan": plan, "plan_expires": expiry})
        claim_ref.set({"claimed_at": now_iso()})
        ref.transaction(lambda d: {**d, "used": int(d.get("used", 0)) + 1} if d else d)
        log_tx(uid, "redeem", credits, code)
        flash("Redeem successfully claim ho gaya!", "success")
        return redirect(url_for("dashboard"))
    return render_template("redeem.html")

def log_tx(uid, kind, amount, note):
    db.reference(f"transactions/{uid}").push({"type": kind, "amount": amount, "note": note, "at": now_iso()})

@app.route("/store")
@login_required
def store():
    plans = db.reference("plans").get() or {}
    active = {k:v for k,v in plans.items() if v.get("active")}
    return render_template("store.html", plans=active)

@app.route("/buy/<plan_id>", methods=["GET", "POST"])
@login_required
def buy(plan_id):
    plan = db.reference(f"plans/{plan_id}").get()
    if not plan or not plan.get("active"):
        abort(404)
    if not UPI_ID:
        flash("Owner ne UPI_ID configure nahi kiya.", "error")
        return redirect(url_for("store"))
    order_id = request.args.get("order") or secrets.token_urlsafe(10)
    amount = float(plan["price"])
    upi_link = f"upi://pay?pa={quote(UPI_ID)}&pn=HACKPHONE&am={amount:.2f}&cu=INR&tn={quote('HACKPHONE '+order_id)}"
    img = qrcode.make(upi_link)
    buf = BytesIO()
    img.save(buf, format="PNG")
    qr_data = "data:image/png;base64," + __import__("base64").b64encode(buf.getvalue()).decode()
    if request.method == "POST":
        # Create a pending request; the user sends the payment screenshot to Telegram.
        db.reference(f"payments/{order_id}").set({
            "uid": session["uid"], "email": current_user().get("email"),
            "plan_id": plan_id, "plan": plan, "amount": amount,
            "status": "pending", "proof_method": "telegram", "created_at": now_iso()
        })
        bot_url = f"https://t.me/ayushcontact_bot?start={quote(order_id)}"
        return redirect(bot_url)
    return render_template("buy.html", plan=plan, qr_data=qr_data, order_id=order_id)

@app.route("/owner/payment/<order_id>/<decision>", methods=["POST"])
@login_required
@owner_required
def decide_payment(order_id, decision):
    if decision not in {"approve", "reject"}:
        abort(400)
    ref = db.reference(f"payments/{order_id}")
    p = ref.get()
    if not p or p.get("status") != "pending":
        flash("Payment request already processed or missing.", "error")
        return redirect(url_for("owner"))
    if decision == "approve":
        uid = p["uid"]
        uref = profile(uid)
        u = uref.get() or {}
        plan = p["plan"]
        days = int(plan.get("days", 0))
        expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat() if days else None
        uref.update({"credits": int(u.get("credits", 0)) + int(plan.get("credits", 0)), "plan": plan.get("type", "Normal"), "plan_expires": expiry})
        log_tx(uid, "purchase", int(plan.get("credits", 0)), f"Approved {plan.get('name')}")
        ref.update({"status": "approved", "reviewed_at": now_iso()})
        flash("Payment approved; plan/credits delivered.", "success")
    else:
        ref.update({"status": "rejected", "reviewed_at": now_iso()})
        flash("Payment rejected.", "success")
    return redirect(url_for("owner"))

@app.route("/owner/proof/<order_id>")
@login_required
@owner_required
def payment_proof(order_id):
    p = db.reference(f"payments/{order_id}").get()
    if not p:
        abort(404)
    return redirect("https://t.me/ayushcontact_bot")


def valid_link_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not any(ch.isspace() for ch in value)
    except Exception:
        return False

def clean_slug(value):
    slug = value.strip().lower().lstrip("/").rstrip("/")
    return slug if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", slug) else ""

@app.route("/links", methods=["GET", "POST"])
@login_required
def links_manager():
    uid = session["uid"]
    ref = db.reference(f"custom_links/{uid}")
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        url = request.form.get("url", "").strip()[:2048]
        slug = clean_slug(request.form.get("slug", ""))
        if not name or not slug or slug in RESERVED_SLUGS or not valid_link_url(url):
            flash("Name, valid slug aur http/https URL check karo. Slug 2-40 chars ka ho sakta hai.", "error")
            return redirect(url_for("links_manager"))
        all_links = ref.get() or {}
        if len(all_links) >= MAX_LINKS_PER_USER:
            flash(f"Link limit reached ({MAX_LINKS_PER_USER}).", "error")
            return redirect(url_for("links_manager"))
        slug_ref = db.reference(f"link_slugs/{slug}")
        if slug_ref.get():
            flash("Ye custom slug pehle se use ho raha hai. Dusra choose karo.", "error")
            return redirect(url_for("links_manager"))
        link_id = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        # Reserve the public slug transactionally to prevent two users claiming it together.
        reserved = slug_ref.transaction(lambda old: old if old else {"uid": uid, "link_id": link_id})
        if not reserved or reserved.get("uid") != uid or reserved.get("link_id") != link_id:
            flash("Ye slug abhi kisi aur ne le liya. Dusra choose karo.", "error")
            return redirect(url_for("links_manager"))
        ref.child(link_id).set({"name": name, "url": url, "slug": slug, "active": True, "clicks": 0, "created_at": now_iso()})
        flash("Custom link create ho gaya!", "success")
        return redirect(url_for("links_manager"))
    all_links = ref.get() or {}
    return render_template("links.html", links=all_links, max_links=MAX_LINKS_PER_USER, base_url=request.host_url.rstrip("/"))

@app.route("/links/<link_id>/edit", methods=["POST"])
@login_required
def edit_custom_link(link_id):
    uid = session["uid"]
    ref = db.reference(f"custom_links/{uid}/{link_id}")
    old = ref.get()
    if not old:
        abort(404)
    name = request.form.get("name", "").strip()[:60]
    url = request.form.get("url", "").strip()[:2048]
    slug = clean_slug(request.form.get("slug", ""))
    if not name or not slug or slug in RESERVED_SLUGS or not valid_link_url(url):
        flash("Name, slug ya URL invalid hai.", "error")
        return redirect(url_for("links_manager"))
    if slug != old.get("slug"):
        new_ref = db.reference(f"link_slugs/{slug}")
        reserved = new_ref.transaction(lambda current: current if current else {"uid": uid, "link_id": link_id})
        if not reserved or reserved.get("uid") != uid or reserved.get("link_id") != link_id:
            flash("Ye slug available nahi hai.", "error")
            return redirect(url_for("links_manager"))
        db.reference(f"link_slugs/{old.get('slug')}").delete()
    ref.update({"name": name, "url": url, "slug": slug})
    flash("Link update ho gaya.", "success")
    return redirect(url_for("links_manager"))

@app.route("/links/<link_id>/toggle", methods=["POST"])
@login_required
def toggle_custom_link(link_id):
    ref = db.reference(f"custom_links/{session['uid']}/{link_id}")
    data = ref.get()
    if not data:
        abort(404)
    ref.update({"active": not bool(data.get("active", True))})
    flash("Link status update ho gaya.", "success")
    return redirect(url_for("links_manager"))

@app.route("/links/<link_id>/delete", methods=["POST"])
@login_required
def delete_custom_link(link_id):
    ref = db.reference(f"custom_links/{session['uid']}/{link_id}")
    data = ref.get()
    if not data:
        abort(404)
    db.reference(f"link_slugs/{data.get('slug', '')}").delete()
    ref.delete()
    flash("Link delete ho gaya.", "success")
    return redirect(url_for("links_manager"))

@app.route("/<slug>")
def public_custom_link(slug):
    slug = clean_slug(slug)
    if not slug or slug in RESERVED_SLUGS:
        abort(404)
    index = db.reference(f"link_slugs/{slug}").get()
    if not index:
        abort(404)
    ref = db.reference(f"custom_links/{index.get('uid')}/{index.get('link_id')}")
    data = ref.get()
    if not data or not data.get("active") or data.get("slug") != slug:
        abort(404)
    ref.child("clicks").transaction(lambda value: int(value or 0) + 1)
    return redirect(data["url"], code=302)

@app.route("/owner/share/create", methods=["POST"])
@login_required
@owner_required
def create_share():
    token = secrets.token_urlsafe(18)
    db.reference(f"share_sessions/{token}").set({"created_by": session["uid"], "status": "waiting", "created_at": now_iso()})
    return jsonify({"url": request.host_url.rstrip("/") + url_for("share", token=token), "token": token})

@app.route("/share/<token>")
def share(token):
    s = db.reference(f"share_sessions/{token}").get()
    if not s:
        abort(404)
    return render_template("share.html", token=token, owner_view=bool(current_user() and current_user().get("is_owner")))

@app.route("/owner/share")
@login_required
@owner_required
def share_admin():
    sessions = db.reference("share_sessions").get() or {}
    return render_template("share_admin.html", sessions=sessions)

@app.route("/share/<token>/stop", methods=["POST"])
def stop_share(token):
    s = db.reference(f"share_sessions/{token}")
    if not s.get():
        abort(404)
    s.update({"status": "stopped", "stopped_at": now_iso()})
    socketio.emit("session_stopped", {"reason": "Sharing session ended"}, room=token)
    return jsonify({"ok": True})

@socketio.on("join_share")
def on_join(data):
    token = (data or {}).get("token", "")
    s = db.reference(f"share_sessions/{token}").get()
    if not s or s.get("status") == "stopped":
        emit("share_error", {"message": "Session unavailable"})
        return
    # Viewers must be the authenticated owner; sharer explicitly joins after browser prompt.
    role = (data or {}).get("role")
    u = current_user()
    if role == "viewer" and (not u or not u.get("is_owner")):
        emit("share_error", {"message": "Owner login required"})
        return
    join_room(token)
    emit("joined", {"role": role}, room=request.sid)

@socketio.on("signal")
def on_signal(data):
    token = (data or {}).get("token", "")
    s = db.reference(f"share_sessions/{token}").get()
    if not s or s.get("status") == "stopped":
        return
    payload = {"description": data.get("description"), "candidate": data.get("candidate")}
    emit("signal", payload, room=token, include_self=False)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))