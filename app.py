from flask import Flask, render_template, request, redirect, url_for, session, g
import sqlite3
from datetime import datetime
import os
from flask_mail import Mail, Message   # ✅ added

app = Flask(__name__)
app.secret_key = "supersecretkey"   # CHANGE this for production

DATABASE = "booking.db"

# ---------------- Mail Config ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'topcon.applicationspecialist@gmail.com'
app.config['MAIL_PASSWORD'] = 'mbyudodbyswygtdl'   # <-- your App Password
app.config['MAIL_DEFAULT_SENDER'] = 'topcon.applicationspecialist@gmail.com'

mail = Mail(app)

# ---------------- Fixed Accounts with Roles ----------------
USERS = {
    "TopconAdmin": {"password": "Topcon1932", "role": "admin"},
    "TopconUser": {"password": "Topcon1932", "role": "user"},
}

# ---------------- DB Helpers ----------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
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


# ---------------- Auth / Routes ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

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

    return render_template(
        "dashboard.html",
        user=session["user"],
        role=session.get("role"),
        total=total,
        malaysia=malaysia,
        singapore=singapore
    )


@app.route("/booking", methods=["GET", "POST"])
def booking():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        country = request.form.get("country", "").strip()
        product_name = request.form.get("product_name", "").strip()
        requested_by = request.form.get("requested_by", "").strip()
        purpose = request.form.get("purpose", "").strip()
        date_of_event = request.form.get("date_of_event", "").strip()
        user_field = request.form.get("user", "").strip()
        competitor_name = request.form.get("competitor_name", "").strip()

        # 🔹 Use requested_by for submitted_by
        submitted_by = requested_by  
        submitted_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db = get_db()
        db.execute("""
            INSERT INTO bookings
            (customer_name, country, product_name, requested_by, purpose, date_of_event, user, competitor_name, submitted_by, submitted_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (customer_name, country, product_name, requested_by, purpose, date_of_event, user_field, competitor_name, submitted_by, submitted_on))
        db.commit()

        # ---------------- Email Notification ----------------
        try:
            msg = Message(
                subject="📌 New Booking Submitted",
                recipients=["ifadzilah@topcon.com"],   # 👈 change to actual email(s)
                body=f"""
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
            )
            mail.send(msg)
            print("✅ Email notification sent!")
        except Exception as e:
            print("❌ Email failed:", e)

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
        customer_name = request.form.get("customer_name", "").strip()
        country = request.form.get("country", "").strip()
        product_name = request.form.get("product_name", "").strip()
        requested_by = request.form.get("requested_by", "").strip()
        purpose = request.form.get("purpose", "").strip()
        date_of_event = request.form.get("date_of_event", "").strip()
        user_field = request.form.get("user", "").strip()
        competitor_name = request.form.get("competitor_name", "").strip()

        db.execute("""
            UPDATE bookings
            SET customer_name=?, country=?, product_name=?, requested_by=?, purpose=?,
                date_of_event=?, user=?, competitor_name=?
            WHERE id=?
        """, (customer_name, country, product_name, requested_by, purpose, date_of_event, user_field, competitor_name, booking_id))
        db.commit()
        return redirect(url_for("bookings"))

    return render_template("edit_booking.html", booking=row)


@app.route("/delete/<int:booking_id>", methods=["POST"])
def delete_booking(booking_id):
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    db = get_db()
    db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    db.commit()
    return redirect(url_for("bookings"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/_clear_all_bookings", methods=["POST"])
def _clear_all_bookings():
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return "Unauthorized", 403

    db = get_db()
    db.execute("DELETE FROM bookings")
    db.commit()

    db.execute("DELETE FROM sqlite_sequence WHERE name='bookings'")
    db.commit()

    try:
        db.execute("VACUUM")
    except Exception:
        pass

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

    return redirect(url_for("bookings"))


if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
    else:
        init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
