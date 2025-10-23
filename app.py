from flask import Flask, render_template, request, redirect, url_for, session, g
import sqlite3
from datetime import datetime
import os
import base64
import requests
from flask_mail import Mail, Message
import threading
import time

# --------------------------------------------------------
#  Flask App Config
# --------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecretkey")  # change in production
DATABASE = "booking.db"

# --------------------------------------------------------
#  Mail Config (prefer environment variables)
# --------------------------------------------------------
# You should set MAIL_USERNAME and MAIL_PASSWORD in Render env vars.
mail_username = os.getenv("MAIL_USERNAME", "topcon.applicationspecialist@gmail.com")
mail_password = os.getenv("MAIL_PASSWORD", "mbyudodbyswygtdl")

app.config['MAIL_SERVER'] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config['MAIL_PORT'] = int(os.getenv("MAIL_PORT", 587))
app.config['MAIL_USE_TLS'] = os.getenv("MAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
app.config['MAIL_USERNAME'] = mail_username
app.config['MAIL_PASSWORD'] = mail_password
app.config['MAIL_DEFAULT_SENDER'] = os.getenv("MAIL_DEFAULT_SENDER", mail_username)

mail = Mail(app)

# --------------------------------------------------------
#  Fixed Accounts with Roles
# --------------------------------------------------------
USERS = {
    "TopconAdmin": {"password": "Topcon1932", "role": "admin"},
    "TopconUser": {"password": "Topcon1932", "role": "user"},
}

# --------------------------------------------------------
#  GitHub Backup Config (from env)
# --------------------------------------------------------
# Expecting:
#   GITHUB_USER     = <github username or org>
#   GITHUB_REPO     = <repo name>
#   GITHUB_TOKEN    = <personal access token with repo:contents write>
GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{DATABASE}"

def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs, flush=True)
    except Exception:
        pass

# --------------------------------------------------------
#  GitHub: Download / Upload (background-safe, with timeouts)
# --------------------------------------------------------
def download_latest_db():
    """Download booking.db from GitHub on startup (if available)."""
    if not (GITHUB_USER and GITHUB_REPO and GITHUB_TOKEN):
        safe_print("⚠️ GitHub env variables not found, skipping DB download.")
        return

    try:
        safe_print("📥 Checking GitHub for latest booking.db ...")
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.get(API_URL, headers=headers, timeout=10)
        if r.status_code == 200:
            content = r.json().get("content")
            if content:
                db_data = base64.b64decode(content)
                with open(DATABASE, "wb") as f:
                    f.write(db_data)
                safe_print("✅ booking.db downloaded successfully from GitHub.")
            else:
                safe_print("⚠️ booking.db content not found in response.")
        else:
            safe_print(f"⚠️ No booking.db found on GitHub (status {r.status_code}).")
    except Exception as e:
        safe_print("❌ Error downloading DB from GitHub:", e)

def upload_latest_db_background():
    """Start a background thread to upload the DB (non-blocking)."""
    if not (GITHUB_USER and GITHUB_REPO and GITHUB_TOKEN):
        safe_print("⚠️ GitHub env variables not found, skipping DB upload.")
        return

    def _upload():
        try:
            safe_print("🚀 Uploading booking.db to GitHub ...")
            headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
            # Get current sha if exists
            get_resp = requests.get(API_URL, headers=headers, timeout=10)
            sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

            with open(DATABASE, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")

            data = {"message": f"Auto-backup booking.db from app ({datetime.utcnow().isoformat()})", "content": encoded}
            if sha:
                data["sha"] = sha

            put_resp = requests.put(API_URL, headers=headers, json=data, timeout=20)
            if put_resp.status_code in (200, 201):
                safe_print("✅ booking.db successfully backed up to GitHub!")
            else:
                safe_print("❌ Failed to upload booking.db:", put_resp.status_code, put_resp.text)
        except Exception as e:
            safe_print("❌ Error uploading DB:", e)

    thread = threading.Thread(target=_upload, daemon=True)
    thread.start()

# Provide a convenience wrapper with small debounce option if desired
_last_upload_time = 0
_upload_lock = threading.Lock()
def upload_latest_db(debounce_seconds=0):
    """Upload DB but avoid spamming GitHub: optional debounce in seconds."""
    global _last_upload_time
    with _upload_lock:
        now = time.time()
        if debounce_seconds and (now - _last_upload_time) < debounce_seconds:
            safe_print("ℹ️ Skipping upload due to debounce.")
            return
        _last_upload_time = now
    upload_latest_db_background()

# --------------------------------------------------------
#  DB Helpers
# --------------------------------------------------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        # Allow access from background threads (we take care to use simple transactions)
        db = g._database = sqlite3.connect(DATABASE, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass

def init_db():
    # Ensure DB file exists / create tables
    with app.app_context():
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT,
                country TEXT,
                product_name TEXT,
                requested_by TEXT,
                purpose TEXT,
                date_of_event TEXT,
                user TEXT,
                competitor_name TEXT,
                submitted_by TEXT,
                submitted_on TEXT
            )
        """)
        db.commit()

# --------------------------------------------------------
#  Helper: async email send
# --------------------------------------------------------
def send_email_async(subject, recipients, body):
    """Send email in background thread to avoid blocking requests."""
    def _send():
        try:
            msg = Message(subject=subject, recipients=recipients, body=body)
            with app.app_context():
                mail.send(msg)
            safe_print("✅ Email notification sent (async).")
        except Exception as e:
            safe_print("❌ Email failed (async):", e)

    threading.Thread(target=_send, daemon=True).start()

# --------------------------------------------------------
#  Routes
# --------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if username in USERS and USERS[username]["password"] == password:
            session.clear()
            session["user"] = username
            session["role"] = USERS[username]["role"]
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    malaysia = db.execute("SELECT COUNT(*) FROM bookings WHERE country='Malaysia'").fetchone()[0]
    singapore = db.execute("SELECT COUNT(*) FROM bookings WHERE country='Singapore'").fetchone()[0]

    return render_template("dashboard.html",
                           user=session["user"],
                           role=session.get("role"),
                           total=total,
                           malaysia=malaysia,
                           singapore=singapore)

@app.route("/booking", methods=["GET", "POST"])
def booking():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        # Defensive get + strip
        customer_name = (request.form.get("customer_name") or "").strip()
        country = (request.form.get("country") or "").strip()
        product_name = (request.form.get("product_name") or "").strip()
        requested_by = (request.form.get("requested_by") or "").strip()
        purpose = (request.form.get("purpose") or "").strip()
        date_of_event = (request.form.get("date_of_event") or "").strip()
        user_field = (request.form.get("user") or "").strip()
        competitor_name = (request.form.get("competitor_name") or "").strip()

        submitted_by = requested_by
        submitted_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            db = get_db()
            db.execute("""
                INSERT INTO bookings
                (customer_name, country, product_name, requested_by, purpose, date_of_event, user, competitor_name, submitted_by, submitted_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (customer_name, country, product_name, requested_by, purpose, date_of_event, user_field, competitor_name, submitted_by, submitted_on))
            db.commit()
        except Exception as e:
            safe_print("❌ DB insert failed:", e)
            return "Internal Server Error", 500

        # Backup DB asynchronously (debounce 1s to avoid rapid commits)
        upload_latest_db(debounce_seconds=1)

        # Email (async)
        subject = "📌 New Booking Submitted"
        recipients = [os.getenv("NOTIFY_EMAIL", "ifadzilah@topcon.com")]
        body = f"""
A new booking has been submitted:

Customer: {customer_name}
Country: {country}
Product: {product_name}
Purpose: {purpose}
Date: {date_of_event}
User: {user_field}
Competitor: {competitor_name}
Submitted by: {submitted_by}
Submitted on: {submitted_on}
"""
        send_email_async(subject, recipients, body)

        return redirect(url_for("bookings"))

    return render_template("booking.html")

@app.route("/bookings")
def bookings():
    if "user" not in session:
        return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("SELECT * FROM bookings ORDER BY id ASC").fetchall()
    return render_template("bookings.html", bookings=rows, role=session.get("role"))

@app.route("/edit/<int:booking_id>", methods=["GET", "POST"])
def edit_booking(booking_id):
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    db = get_db()
    row = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if row is None:
        return "Booking not found", 404

    if request.method == "POST":
        customer_name = (request.form.get("customer_name") or "").strip()
        country = (request.form.get("country") or "").strip()
        product_name = (request.form.get("product_name") or "").strip()
        requested_by = (request.form.get("requested_by") or "").strip()
        purpose = (request.form.get("purpose") or "").strip()
        date_of_event = (request.form.get("date_of_event") or "").strip()
        user_field = (request.form.get("user") or "").strip()
        competitor_name = (request.form.get("competitor_name") or "").strip()

        try:
            db.execute("""
                UPDATE bookings
                SET customer_name=?, country=?, product_name=?, requested_by=?, purpose=?,
                    date_of_event=?, user=?, competitor_name=?
                WHERE id=?
            """, (customer_name, country, product_name, requested_by, purpose, date_of_event, user_field, competitor_name, booking_id))
            db.commit()
        except Exception as e:
            safe_print("❌ DB update failed:", e)
            return "Internal Server Error", 500

        upload_latest_db(debounce_seconds=1)
        return redirect(url_for("bookings"))

    return render_template("edit_booking.html", booking=row)

@app.route("/delete/<int:booking_id>", methods=["POST"])
def delete_booking(booking_id):
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    try:
        db = get_db()
        db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        db.commit()
    except Exception as e:
        safe_print("❌ DB delete failed:", e)
        return "Internal Server Error", 500

    upload_latest_db(debounce_seconds=1)
    return redirect(url_for("bookings"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --------------------------------------------------------
#  Maintenance Routes (Admin)
# --------------------------------------------------------
@app.route("/_clear_all_bookings", methods=["POST"])
def _clear_all_bookings():
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    try:
        db = get_db()
        db.execute("DELETE FROM bookings")
        db.commit()
        db.execute("DELETE FROM sqlite_sequence WHERE name='bookings'")
        db.commit()
        try:
            db.execute("VACUUM")
        except Exception:
            pass
    except Exception as e:
        safe_print("❌ Clear all failed:", e)
        return "Internal Server Error", 500

    upload_latest_db(debounce_seconds=1)
    return redirect(url_for("bookings"))

@app.route("/_resequence_bookings", methods=["POST"])
def _resequence_bookings():
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    db = get_db()
    rows = db.execute("""
        SELECT customer_name, country, product_name, requested_by, purpose, date_of_event, user, competitor_name, submitted_by, submitted_on
        FROM bookings
        ORDER BY id
    """).fetchall()

    try:
        db.execute("DELETE FROM bookings")
        db.commit()
        for r in rows:
            db.execute("""
                INSERT INTO bookings
                (customer_name, country, product_name, requested_by, purpose, date_of_event, user, competitor_name, submitted_by, submitted_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r["customer_name"], r["country"], r["product_name"], r["requested_by"],
                  r["purpose"], r["date_of_event"], r["user"], r["competitor_name"],
                  r["submitted_by"], r["submitted_on"]))
        db.commit()
        db.execute("DELETE FROM sqlite_sequence WHERE name='bookings'")
        db.commit()
        try:
            db.execute("VACUUM")
        except Exception:
            pass
    except Exception as e:
        safe_print("❌ Resequence failed:", e)
        return "Internal Server Error", 500

    upload_latest_db(debounce_seconds=1)
    return redirect(url_for("bookings"))

# --------------------------------------------------------
#  App Entry
# --------------------------------------------------------
if __name__ == "__main__":
    # Attempt to download latest DB from GitHub (if configured)
    download_latest_db()
    # Ensure DB and table exist
    init_db()
    # Show some debug info (Render logs)
    safe_print(f"🔧 GitHub config: user={GITHUB_USER}, repo={GITHUB_REPO}, token_set={'yes' if bool(GITHUB_TOKEN) else 'no'}")
    safe_print(f"🔧 Mail config: username={app.config.get('MAIL_USERNAME')}, mail_env_set={'yes' if bool(mail_password) else 'no'}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
