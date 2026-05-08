from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file
from datetime import datetime
import sqlite3
import qrcode
import io
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask_cors import CORS
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_default_dev_key')
CORS(app)

# Database file stored in /tmp for Render compatibility
DB_FILE = os.path.join('/tmp', 'canyon_bookings.db')

ROOM_TYPES = {
    'Deluxe Canyon View': {
        'price': 350,
        'capacity': 2,
        'description': 'Spacious room with breathtaking canyon views, king-size bed, and private balcony.',
        'amenities': ['King Bed', 'Private Balcony', 'Mini Bar', 'Free WiFi', 'Room Service']
    },
    'Premium Suite': {
        'price': 550,
        'capacity': 4,
        'description': 'Luxurious suite with separate living area, jacuzzi, and panoramic canyon views.',
        'amenities': ['2 Bedrooms', 'Living Room', 'Jacuzzi', 'Fireplace', 'Butler Service']
    },
    'Presidential Suite': {
        'price': 1200,
        'capacity': 6,
        'description': 'Ultimate luxury with three bedrooms, private pool, and personal butler service.',
        'amenities': ['3 Bedrooms', 'Private Pool', 'Butler Service', 'Wine Cellar', 'Helicopter Pad Access']
    },
    'Standard Room': {
        'price': 200,
        'capacity': 2,
        'description': 'Comfortable room with modern amenities and city views.',
        'amenities': ['Queen Bed', 'City View', 'Free WiFi', 'Coffee Maker', 'Safe Box']
    },
    'Family Suite': {
        'price': 450,
        'capacity': 5,
        'description': 'Perfect for families with two bedrooms and kid-friendly amenities.',
        'amenities': ['2 Bedrooms', 'Kitchen', 'Kids Area', 'Game Console', 'Laundry']
    }
}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','customer'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        room_type TEXT NOT NULL,
        check_in DATE NOT NULL,
        check_out DATE NOT NULL,
        time TEXT NOT NULL DEFAULT '12:00',
        guests INTEGER NOT NULL DEFAULT 1,
        special_requests TEXT,
        total_price REAL NOT NULL,
        booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'active',
        time_in TEXT,
        time_out TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')

    # Create default accounts if none exist
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        # Admin
        c.execute("INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
                  ('admin@canyon.com', generate_password_hash('admin123'), 'Admin Canyon', 'admin'))
        # 5 customers
        customers = [
            ('alice@example.com', 'alice123', 'Alice Johnson'),
            ('bob@example.com', 'bob123', 'Bob Williams'),
            ('carol@example.com', 'carol123', 'Carol Brown'),
            ('dave@example.com', 'dave123', 'Dave Jones'),
            ('eve@example.com', 'eve123', 'Eve Davis')
        ]
        for email, pwd, name in customers:
            c.execute("INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, 'customer')",
                      (email, generate_password_hash(pwd), name))

    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ----------------------------------------------------------------
# Public pages
# ----------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', user=session)

@app.route('/accommodation')
def accommodation():
    return render_template('accommodation.html', room_types=ROOM_TYPES, user=session)

@app.route('/about')
def about():
    return render_template('about.html', user=session)

@app.route('/contact')
def contact():
    return render_template('contact.html', user=session)

@app.route('/rules')
@login_required
def rules():
    if session.get('role') != 'customer':
        flash('Access denied.', 'error')
        return redirect(url_for('admin') if session.get('role') == 'admin' else url_for('index'))
    return render_template('rules.html', user=session)

# ----------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['email'] = user['email']
            session['name'] = user['name']
            session['role'] = user['role']
            flash('Logged in successfully.', 'success')
            if user['role'] == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('booking_page'))
        else:
            flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm = request.form['confirm']
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            conn.close()
            flash('Email already registered.', 'error')
            return render_template('register.html')
        try:
            conn.execute("INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, 'customer')",
                         (email, generate_password_hash(password), name))
            conn.commit()
            conn.close()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash('Registration failed. Please try again.', 'error')
            return render_template('register.html')
    return render_template('register.html')

# ----------------------------------------------------------------
# Booking (customer only)
# ----------------------------------------------------------------
@app.route('/booking')
@login_required
def booking_page():
    if session.get('role') == 'admin':
        flash('Admins cannot book appointments.', 'error')
        return redirect(url_for('admin'))
    return render_template('booking.html', room_types=ROOM_TYPES, user=session)

@app.route('/api/check_availability', methods=['POST'])
@login_required
def check_availability():
    if session.get('role') != 'customer':
        return jsonify({'available': False, 'message': 'Only customers can check availability.'})
    data = request.json
    check_in = data['check_in']
    check_out = data['check_out']
    room_type = data['room_type']
    guests = int(data.get('guests', 1))
    time_slot = data.get('time', '12:00')

    nights = calculate_nights(check_in, check_out)
    price_per_night = ROOM_TYPES[room_type]['price']
    total = price_per_night * nights

    # Duplicate slot check
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM bookings WHERE room_type=? AND check_in=? AND time=? AND status='active'",
        (room_type, check_in, time_slot)
    ).fetchone()
    conn.close()
    if existing:
        return jsonify({'available': False, 'message': 'This time slot is already booked.'})

    return jsonify({'available': True, 'price_per_night': price_per_night, 'total_price': total, 'nights': nights})

@app.route('/api/create_booking', methods=['POST'])
@login_required
def create_booking():
    if session.get('role') != 'customer':
        return jsonify({'success': False, 'message': 'Admins cannot create bookings.'})
    data = request.json
    try:
        check_in = data['check_in']
        check_out = data['check_out']
        time_slot = data.get('time', '12:00')
        room_type = data['room_type']
        guests = int(data.get('guests', 1))
        nights = calculate_nights(check_in, check_out)
        price = ROOM_TYPES[room_type]['price'] * nights

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM bookings WHERE room_type=? AND check_in=? AND time=? AND status='active'",
            (room_type, check_in, time_slot)
        ).fetchone()
        if existing:
            conn.close()
            return jsonify({'success': False, 'message': 'Time slot already taken.'})

        conn.execute('''INSERT INTO bookings 
            (user_id, name, email, phone, room_type, check_in, check_out, time, guests, special_requests, total_price)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (session['user_id'], data['name'], data['email'], data['phone'], room_type,
             check_in, check_out, time_slot, guests, data.get('special_requests', ''), price))
        conn.commit()
        booking_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'booking_id': booking_id, 'total_price': price, 'message': 'Booking confirmed!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/my-bookings')
@login_required
def my_bookings():
    if session.get('role') != 'customer':
        return redirect(url_for('admin'))
    conn = get_db()
    bookings = conn.execute(
        "SELECT * FROM bookings WHERE user_id=? ORDER BY booking_date DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings, user=session)

# ----------------------------------------------------------------
# QR Code (customer & admin)
# ----------------------------------------------------------------
@app.route('/booking/qr/<int:booking_id>')
@login_required
def booking_qr(booking_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    conn.close()
    if not booking:
        return "Booking not found", 404
    if session['role'] != 'admin' and booking['user_id'] != session['user_id']:
        return "Unauthorized", 403

    qr_data = str(booking_id)
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# ----------------------------------------------------------------
# Admin scanner & time recording
# ----------------------------------------------------------------
@app.route('/scanner')
@admin_required
def scanner():
    return render_template('scanner.html', user=session)

@app.route('/api/record_time_in', methods=['POST'])
@admin_required
def record_time_in():
    data = request.json
    booking_id = int(data['booking_id'])
    now = now = ph_now()
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking or booking['status'] != 'active':
        conn.close()
        return jsonify({'success': False, 'message': 'Booking not found or not active.'})
    if booking['time_in']:
        conn.close()
        return jsonify({'success': False, 'message': 'Time‑in already recorded.'})
    conn.execute("UPDATE bookings SET time_in=? WHERE id=?", (now, booking_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'time_in': now, 'message': 'Time‑in recorded.'})

@app.route('/api/record_time_out', methods=['POST'])
@admin_required
def record_time_out():
    data = request.json
    booking_id = int(data['booking_id'])
    now = now = ph_now()
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking or booking['status'] != 'active':
        conn.close()
        return jsonify({'success': False, 'message': 'Booking not found or not active.'})
    if not booking['time_in']:
        conn.close()
        return jsonify({'success': False, 'message': 'Time‑in must be recorded first.'})
    if booking['time_out']:
        conn.close()
        return jsonify({'success': False, 'message': 'Time‑out already recorded.'})
    conn.execute("UPDATE bookings SET time_out=? WHERE id=?", (now, booking_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'time_out': now, 'message': 'Time‑out recorded.'})

# ----------------------------------------------------------------
# Admin dashboard
# ----------------------------------------------------------------
@app.route('/admin')
@admin_required
def admin():
    return render_template('admin.html', room_types=ROOM_TYPES, user=session)

@app.route('/api/bookings')
@admin_required
def get_bookings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM bookings ORDER BY booking_date DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/cancel_booking', methods=['POST'])
@login_required
def cancel_booking():
    data = request.json
    booking_id = int(data['booking_id'])
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        return jsonify({'success': False, 'message': 'Booking not found.'})
    if session['role'] != 'admin' and booking['user_id'] != session['user_id']:
        conn.close()
        return jsonify({'success': False, 'message': 'Unauthorized.'}), 403

    # Block cancellation if already checked in (time_in exists)
    if booking['time_in']:
        conn.close()
        return jsonify({'success': False, 'message': 'Booking cannot be cancelled after check‑in.'})

    conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Booking cancelled.'})

@app.route('/api/edit_booking', methods=['POST'])
@admin_required
def edit_booking():
    data = request.json
    booking_id = int(data['booking_id'])
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        return jsonify({'success': False, 'message': 'Booking not found.'})

    new_room = data.get('room_type')
    new_check_in = data.get('check_in')
    new_time = data.get('time')
    if new_room and new_check_in and new_time:
        existing = conn.execute(
            "SELECT id FROM bookings WHERE room_type=? AND check_in=? AND time=? AND status='active' AND id!=?",
            (new_room, new_check_in, new_time, booking_id)
        ).fetchone()
        if existing:
            conn.close()
            return jsonify({'success': False, 'message': 'Time slot already taken.'})

    updates = {}
    for field in ['name', 'email', 'phone', 'room_type', 'check_in', 'check_out', 'time', 'guests', 'special_requests']:
        if field in data:
            updates[field] = data[field]

    final_check_in = updates.get('check_in', booking['check_in'])
    final_check_out = updates.get('check_out', booking['check_out'])
    final_room = updates.get('room_type', booking['room_type'])
    nights = calculate_nights(final_check_in, final_check_out)
    final_price = ROOM_TYPES[final_room]['price'] * nights

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values())
    values.append(final_price)
    values.append(booking_id)

    conn.execute(f"UPDATE bookings SET {set_clause}, total_price=? WHERE id=?", values)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Booking updated.', 'new_total': final_price})

@app.route('/api/stats')
@admin_required
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM bookings WHERE status='active'").fetchone()[0]
    cancelled = conn.execute("SELECT COUNT(*) FROM bookings WHERE status='cancelled'").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status='active'").fetchone()[0]
    avg_price = round(revenue / active, 2) if active > 0 else 0
    conn.close()
    return jsonify({
        'total': total,
        'active': active,
        'cancelled': cancelled,
        'revenue': revenue,
        'average_per_active': avg_price
    })

@app.route('/booking-confirmation')
@login_required
def booking_confirmation():
    return render_template('confirmation.html', user=session)

def calculate_nights(check_in, check_out):
    fmt = "%Y-%m-%d"
    d1 = datetime.strptime(check_in, fmt)
    d2 = datetime.strptime(check_out, fmt)
    return (d2 - d1).days

def ph_now():
    """Return current Philippine time (UTC+8) as a datetime string."""
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)