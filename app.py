from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file
from datetime import datetime, timedelta
import sqlite3
import qrcode
import io
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_default_dev_key')
CORS(app)

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

ROOM_INVENTORY = {
    'Deluxe Canyon View': 20,
    'Premium Suite': 15,
    'Presidential Suite': 5,
    'Standard Room': 40,
    'Family Suite': 10
}

ROOM_PREFIX = {
    'Deluxe Canyon View': 'DC',
    'Premium Suite': 'PS',
    'Presidential Suite': 'PR',
    'Standard Room': 'SR',
    'Family Suite': 'FS'
}

ACTIVITIES = {
    'Snorkeling': 500,
    'Kayaking': 400,
    'Sunset Cruise': 1200,
    'Jet Ski': 1500,
    'Beach Volleyball': 200,
    'Paddleboarding': 600
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

    c.execute('''CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_number TEXT UNIQUE NOT NULL,
        room_type TEXT NOT NULL
    )''')

    c.execute("SELECT COUNT(*) FROM rooms")
    if c.fetchone()[0] == 0:
        for room_type, count in ROOM_INVENTORY.items():
            prefix = ROOM_PREFIX[room_type]
            for i in range(1, count + 1):
                room_number = f"{prefix}-{i:02d}"
                c.execute("INSERT INTO rooms (room_number, room_type) VALUES (?, ?)",
                          (room_number, room_type))

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
        downpayment REAL NOT NULL DEFAULT 0,
        balance REAL NOT NULL DEFAULT 0,
        payment_method TEXT DEFAULT '',
        activities TEXT DEFAULT '',
        room_number TEXT,
        booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'active',
        time_in TEXT,
        time_out TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')

    # Add 'paid' column if missing
    try:
        c.execute("ALTER TABLE bookings ADD COLUMN paid INTEGER DEFAULT 0")
    except:
        pass

    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
                  ('admin@canyon.com', generate_password_hash('admin123'), 'Admin Canyon', 'admin'))
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

def ph_now():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

def assign_room(room_type, check_in, check_out):
    conn = get_db()
    rooms = conn.execute("SELECT room_number FROM rooms WHERE room_type = ?", (room_type,)).fetchall()
    if not rooms:
        conn.close()
        return None
    room_numbers = [r['room_number'] for r in rooms]
    placeholders = ','.join('?' for _ in room_numbers)
    query = f'''SELECT DISTINCT room_number FROM bookings
                WHERE room_number IN ({placeholders})
                  AND status = 'active'
                  AND check_in < ?
                  AND check_out > ?'''
    params = room_numbers + [check_out, check_in]
    used_rooms = {row['room_number'] for row in conn.execute(query, params).fetchall()}
    conn.close()
    for rn in room_numbers:
        if rn not in used_rooms:
            return rn
    return None

def calculate_nights(check_in, check_out):
    fmt = "%Y-%m-%d"
    d1 = datetime.strptime(check_in, fmt)
    d2 = datetime.strptime(check_out, fmt)
    return (d2 - d1).days

# ------------------ Public pages ------------------
@app.route('/')
def index():
    return render_template('index.html', user=session)

@app.route('/accommodation')
def accommodation():
    return render_template('accommodation.html', room_types=ROOM_TYPES, inventory=ROOM_INVENTORY, activities=ACTIVITIES, user=session)

@app.route('/about')
def about():
    return render_template('about.html', user=session)

@app.route('/contact')
def contact():
    return render_template('contact.html', user=session)

@app.route('/gallery')
def gallery():
    return render_template('gallery.html', user=session)

@app.route('/faq')
def faq():
    return render_template('faq.html', user=session)

@app.route('/rules')
@login_required
def rules():
    if session.get('role') != 'customer':
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    return render_template('rules.html', user=session)

# ------------------ Authentication ------------------
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
            return redirect(url_for('index'))  # <-- Everyone lands on homepage
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
        except:
            flash('Registration failed. Please try again.', 'error')
            return render_template('register.html')
    return render_template('register.html')

# ------------------ Room availability API (for Accommodation page) ------------------
@app.route('/api/room_counts')
def room_counts():
    conn = get_db()
    counts = {}
    today = date.today().isoformat()
    for room_type in ROOM_TYPES:
        total = ROOM_INVENTORY[room_type]
        used = conn.execute("SELECT COUNT(*) FROM bookings WHERE room_type=? AND status='active' AND check_out > ?", (room_type, today)).fetchone()[0]
        counts[room_type] = max(0, total - used)
    conn.close()
    return jsonify(counts)

# ------------------ Booking (customer) ------------------
@app.route('/booking')
@login_required
def booking_page():
    if session.get('role') == 'admin':
        flash('Admins cannot book appointments.', 'error')
        return redirect(url_for('index'))
    return render_template('booking.html', room_types=ROOM_TYPES, activities=ACTIVITIES, user=session)

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

    if datetime.strptime(check_out, "%Y-%m-%d") <= datetime.strptime(check_in, "%Y-%m-%d"):
        return jsonify({'available': False, 'message': 'Check‑out must be after check‑in.'})

    nights = calculate_nights(check_in, check_out)
    price_per_night = ROOM_TYPES[room_type]['price']
    total_room = price_per_night * nights

    available_room = assign_room(room_type, check_in, check_out)
    if not available_room:
        return jsonify({'available': False, 'message': 'No rooms available for the selected dates.'})

    conn = get_db()
    total_rooms = conn.execute("SELECT COUNT(*) FROM rooms WHERE room_type = ?", (room_type,)).fetchone()[0]
    used_rooms = conn.execute('''SELECT COUNT(*) FROM bookings
                                WHERE room_type = ? AND status = 'active'
                                AND check_in < ? AND check_out > ?''',
                              (room_type, check_out, check_in)).fetchone()[0]
    conn.close()
    available_count = max(0, total_rooms - used_rooms)

    activities_total = 0
    selected_activities = data.get('activities', [])
    for act in selected_activities:
        if act in ACTIVITIES:
            activities_total += ACTIVITIES[act]

    grand_total = total_room + activities_total

    return jsonify({
        'available': True,
        'rooms_left': available_count,
        'price_per_night': price_per_night,
        'room_total': total_room,
        'activities_total': activities_total,
        'total_price': grand_total,
        'nights': nights
    })

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
        price_room = ROOM_TYPES[room_type]['price'] * nights

        room_number = assign_room(room_type, check_in, check_out)
        if not room_number:
            return jsonify({'success': False, 'message': 'No rooms available for the selected dates.'})

        selected_activities = data.get('activities', [])
        activities_total = 0
        for act in selected_activities:
            if act in ACTIVITIES:
                activities_total += ACTIVITIES[act]

        grand_total = price_room + activities_total

        DEPOSIT_RATE = 0.30
        downpayment = round(grand_total * DEPOSIT_RATE, 2)
        balance = round(grand_total - downpayment, 2)
        payment_method = data.get('payment_method', 'card')
        if payment_method not in ['gcash', 'card']:
            payment_method = 'card'

        conn = get_db()
        conn.execute('''INSERT INTO bookings 
            (user_id, name, email, phone, room_type, check_in, check_out, time, guests,
             special_requests, total_price, downpayment, balance, payment_method, activities, room_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (session['user_id'], data['name'], data['email'], data['phone'], room_type,
             check_in, check_out, time_slot, guests,
             data.get('special_requests', ''), grand_total, downpayment, balance, payment_method,
             ','.join(selected_activities), room_number))
        conn.commit()
        booking_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({
            'success': True,
            'booking_id': booking_id,
            'total_price': grand_total,
            'downpayment': downpayment,
            'balance': balance,
            'message': 'Booking confirmed!'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ------------------ My Bookings ------------------
@app.route('/my-bookings')
@login_required
def my_bookings():
    if session.get('role') != 'customer':
        return redirect(url_for('index'))
    conn = get_db()
    bookings = conn.execute(
        "SELECT * FROM bookings WHERE user_id=? ORDER BY booking_date DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings, user=session)

# ------------------ Receipt ------------------
@app.route('/receipt/<int:booking_id>')
@login_required
def receipt(booking_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    conn.close()
    if not booking:
        flash('Booking not found.', 'error')
        return redirect(url_for('my_bookings') if session['role'] == 'customer' else url_for('admin'))
    if session['role'] != 'admin' and booking['user_id'] != session['user_id']:
        flash('Unauthorized.', 'error')
        return redirect(url_for('index'))
    activities_list = [a.strip() for a in booking['activities'].split(',') if a.strip()]
    activities_details = {a: ACTIVITIES.get(a, 0) for a in activities_list}
    nights = calculate_nights(booking['check_in'], booking['check_out'])
    return render_template('receipt.html', booking=booking, activities_details=activities_details,
                           user=session, room_types=ROOM_TYPES, nights=nights)

# ------------------ QR Code ------------------
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

# ------------------ Admin Scanner & Time Recording ------------------
@app.route('/scanner')
@admin_required
def scanner():
    return render_template('scanner.html', user=session)

@app.route('/api/record_time_in', methods=['POST'])
@admin_required
def record_time_in():
    data = request.json
    booking_id = int(data['booking_id'])
    now = ph_now()
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
    now = ph_now()
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

# ------------------ Admin Dashboard ------------------
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
    new_check_out = data.get('check_out')
    if new_room and new_check_in and new_check_out:
        if new_room != booking['room_type']:
            new_room_number = assign_room(new_room, new_check_in, new_check_out)
            if not new_room_number:
                conn.close()
                return jsonify({'success': False, 'message': 'No rooms available for the new selection.'})
            data['room_number'] = new_room_number

    updates = {}
    for field in ['name', 'email', 'phone', 'room_type', 'check_in', 'check_out', 'time', 'guests', 'special_requests', 'activities', 'room_number']:
        if field in data:
            updates[field] = data[field]

    final_check_in = updates.get('check_in', booking['check_in'])
    final_check_out = updates.get('check_out', booking['check_out'])
    final_room = updates.get('room_type', booking['room_type'])
    nights = calculate_nights(final_check_in, final_check_out)
    price_room = ROOM_TYPES[final_room]['price'] * nights

    activities_str = updates.get('activities', booking['activities'])
    act_list = [a.strip() for a in activities_str.split(',') if a.strip()]
    act_total = sum(ACTIVITIES.get(a, 0) for a in act_list)
    final_price = price_room + act_total

    DEPOSIT_RATE = 0.30
    downpayment = round(final_price * DEPOSIT_RATE, 2)
    balance = round(final_price - downpayment, 2)
    updates['downpayment'] = downpayment
    updates['balance'] = balance

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values())
    values.append(booking_id)

    conn.execute(f"UPDATE bookings SET {set_clause}, total_price=? WHERE id=?", values)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Booking updated.', 'new_total': final_price})

@app.route('/api/mark_paid', methods=['POST'])
@admin_required
def mark_paid():
    data = request.json
    booking_id = int(data['booking_id'])
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking or booking['status'] != 'active':
        conn.close()
        return jsonify({'success': False, 'message': 'Booking not found or not active.'})
    if booking['paid']:
        conn.close()
        return jsonify({'success': False, 'message': 'Already marked as paid.'})
    conn.execute("UPDATE bookings SET paid=1, balance=0 WHERE id=?", (booking_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Booking marked as fully paid.'})

@app.route('/api/stats')
@admin_required
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM bookings WHERE status='active'").fetchone()[0]
    cancelled = conn.execute("SELECT COUNT(*) FROM bookings WHERE status='cancelled'").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status='active'").fetchone()[0]
    avg_price = round(revenue / active, 2) if active > 0 else 0
    act_revenue = 0
    rows = conn.execute("SELECT activities FROM bookings WHERE status='active' AND activities != ''").fetchall()
    for r in rows:
        for a in r['activities'].split(','):
            a = a.strip()
            if a in ACTIVITIES:
                act_revenue += ACTIVITIES[a]
    conn.close()
    return jsonify({
        'total': total,
        'active': active,
        'cancelled': cancelled,
        'revenue': revenue,
        'activities_revenue': act_revenue,
        'average_per_active': avg_price
    })

@app.route('/booking-confirmation')
@login_required
def booking_confirmation():
    return render_template('confirmation.html', user=session)

# ------------------ Init DB on startup ------------------
init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)