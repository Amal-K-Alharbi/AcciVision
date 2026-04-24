import os
import cv2
import time
import uuid
import base64
import sqlite3
import hashlib
import threading
import numpy as np
from datetime import datetime
from flask import (
    Flask, render_template, request, Response,
    jsonify, session, redirect, url_for
)
from ultralytics import YOLO
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'accivision_secret_key_2024'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB max
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'webm'}

BASE_DIR = os.path.dirname(__file__)
ACCIDENTS_DIR = os.path.join(BASE_DIR, 'static', 'accidents')
DB_PATH = os.path.join(BASE_DIR, 'accivision.db')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(ACCIDENTS_DIR, exist_ok=True)

# ─── Load Model & Labels ─────────────────────────────────────
MODEL_PATH = os.path.join(BASE_DIR, 'best.pt')
LABELS_PATH = os.path.join(BASE_DIR, 'coco.txt')

model = YOLO(MODEL_PATH)

with open(LABELS_PATH, 'r') as f:
    class_list = [line.strip() for line in f.read().strip().split('\n')]

# ─── Global State ─────────────────────────────────────────────
camera_active = False
current_video_path = None
video_active = False
accident_flag = False          # polled by browser for sound
accident_flag_lock = threading.Lock()

# Snapshot cooldown: save max 1 screenshot per 30 seconds
last_snapshot_time = 0

# Pre-compute JPEG encode params once
ENCODE_PARAM = [int(cv2.IMWRITE_JPEG_QUALITY), 60]


# ─── Database ─────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS accidents (
        id TEXT PRIMARY KEY,
        image TEXT NOT NULL,
        timestamp REAL NOT NULL,
        notified INTEGER DEFAULT 0,
        responded INTEGER DEFAULT 0,
        closed INTEGER DEFAULT 0,
        reported_at REAL,
        responded_at REAL
    )''')
    user_columns = [row['name'] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if 'role' not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    accident_columns = [row['name'] for row in conn.execute("PRAGMA table_info(accidents)").fetchall()]
    if 'responded' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN responded INTEGER DEFAULT 0")
    if 'closed' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN closed INTEGER DEFAULT 0")
    if 'reported_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN reported_at REAL")
    if 'responded_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN responded_at REAL")

    conn.commit()
    conn.close()


init_db()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def get_user_role(email):
    conn = get_db()
    row = conn.execute('SELECT role FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    return row['role'] if row and row['role'] in {'admin', 'responder'} else 'admin'


def get_current_role():
    return session.get('user_role', 'admin')


def get_alert_counts(conn=None):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    counts = conn.execute(
        '''
        SELECT
            SUM(CASE WHEN notified = 1 AND closed = 0 THEN 1 ELSE 0 END) AS active_alerts_count,
            SUM(CASE WHEN responded = 1 AND closed = 0 THEN 1 ELSE 0 END) AS responded_cases_count
        FROM accidents
        '''
    ).fetchone()

    if should_close:
        conn.close()

    return {
        'active_alerts_count': counts['active_alerts_count'] or 0,
        'responded_cases_count': counts['responded_cases_count'] or 0,
    }


def get_average_response_time(conn=None):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    stats = conn.execute(
        '''
        SELECT
            COUNT(*) AS responded_total,
            AVG(responded_at - reported_at) AS average_response_seconds
        FROM accidents
        WHERE reported_at IS NOT NULL
          AND responded_at IS NOT NULL
          AND responded_at >= reported_at
        '''
    ).fetchone()

    if should_close:
        conn.close()

    average_seconds = stats['average_response_seconds']
    if average_seconds is None:
        return {
            'avg_response_seconds': None,
            'avg_response_time_label': 'No data available'
        }

    return {
        'avg_response_seconds': average_seconds,
        'avg_response_time_label': format_duration(average_seconds)
    }


def get_active_alerts_count(conn=None):
    return get_alert_counts(conn)['active_alerts_count']


def get_responded_cases_count(conn=None):
    return get_alert_counts(conn)['responded_cases_count']


def build_recent_events(conn=None, limit=6):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    rows = conn.execute(
        '''
        SELECT id, timestamp, notified, responded, closed
        FROM accidents
        ORDER BY timestamp DESC
        LIMIT ?
        ''',
        (limit,)
    ).fetchall()

    recent_events = []
    for index, row in enumerate(rows, start=1):
        status = build_incident_status(bool(row['notified']), bool(row['responded']), bool(row['closed']))
        recent_events.append({
            'event_id': f"EVT-{datetime.fromtimestamp(row['timestamp']).strftime('%Y%m%d')}-{index:03d}",
            'location': f"Monitored Zone {((index - 1) % 4) + 1}",
            'severity': 'Critical' if row['notified'] and not row['closed'] else 'High' if row['responded'] else 'Medium' if row['closed'] else 'Low',
            'status': status,
            'time': get_elapsed_time(row['timestamp']),
        })

    if should_close:
        conn.close()

    if recent_events:
        return recent_events

    return [
        {'event_id': 'EVT-DEMO-001', 'location': 'North Corridor Camera 04', 'severity': 'Critical', 'status': 'Active', 'time': '2 min ago'},
        {'event_id': 'EVT-DEMO-002', 'location': 'Main St & 4th Ave', 'severity': 'High', 'status': 'Responded', 'time': '14 min ago'},
        {'event_id': 'EVT-DEMO-003', 'location': 'Downtown Sector 2', 'severity': 'Medium', 'status': 'Closed', 'time': '38 min ago'},
    ]


def build_incident_status(notified, responded, closed):
    if closed:
        return 'Closed'
    if responded:
        return 'Responded'
    if notified:
        return 'Active'
    return 'Pending'


def serialize_accident(row):
    notified = bool(row['notified'])
    responded = bool(row['responded'])
    closed = bool(row['closed'])
    return {
        'id': row['id'],
        'image': row['image'],
        'elapsed': get_elapsed_time(row['timestamp']),
        'date': datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
        'notified': notified,
        'responded': responded,
        'closed': closed,
        'status': build_incident_status(notified, responded, closed)
    }


def build_dashboard_context():
    role = get_current_role()
    conn = get_db()
    alert_counts = get_alert_counts(conn)
    response_metrics = get_average_response_time(conn)
    recent_events = build_recent_events(conn)
    events_today_count = conn.execute(
        '''
        SELECT COUNT(*) AS total
        FROM accidents
        WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
        '''
    ).fetchone()['total'] or 0
    conn.close()

    user_email = session.get('user_email', '')
    user_name = user_email.split('@', 1)[0].replace('.', ' ').title() if user_email else role.title()
    return {
        **alert_counts,
        **response_metrics,
        'cameras_online_count': 124,
        'cameras_total_count': 128,
        'events_today_count': events_today_count,
        'recent_events': recent_events,
        'current_role': role,
        'dashboard_title': 'Dashboard',
        'user_name': user_name,
        'user_role_label': role.title(),
        'alerts_url': url_for('alerts_page'),
        'alerts_label': 'Active Alerts',
    }


def render_dashboard_page():
    return render_template('home.html', **build_dashboard_context())


def get_post_login_endpoint():
    return 'dashboard'


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.after_request
def add_no_cache_headers(response):
    if 'user_id' in session and request.endpoint != 'static':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


def responder_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if get_current_role() != 'responder':
            return "Access Denied", 403
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if get_current_role() != 'admin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ─── Helpers ──────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_elapsed_time(timestamp):
    diff = time.time() - timestamp
    if diff < 60:
        return f"{int(diff)} seconds ago"
    elif diff < 3600:
        mins = int(diff // 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif diff < 86400:
        hours = int(diff // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(diff // 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


def format_duration(seconds):
    total_seconds = max(int(round(seconds)), 0)
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)

    if hours > 0:
        return f"{hours} hr {remaining_minutes} min"
    if minutes > 0:
        return f"{minutes} min {remaining_seconds} sec"
    return f"{remaining_seconds} sec"


def save_snapshot_background(frame_copy):
    """Save accident snapshot in a background thread — never blocks video."""
    accident_id = str(uuid.uuid4())[:8]
    filename = f"accident_{accident_id}.jpg"
    filepath = os.path.join(ACCIDENTS_DIR, filename)

    cv2.imwrite(filepath, frame_copy, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

    conn = get_db()
    conn.execute(
        'INSERT INTO accidents (id, image, timestamp, notified) VALUES (?, ?, ?, 0)',
        (accident_id, filename, time.time())
    )
    conn.commit()
    conn.close()


def process_frame(frame):
    """Run YOLO detection on a single frame and draw results.
    OPTIMIZED: no pandas, direct numpy array access, smaller inference size."""
    global accident_flag

    frame = cv2.resize(frame, (1020, 500))

    # Same as original code — no conf filter so all detections come through
    results = model.predict(frame, verbose=False)

    boxes = results[0].boxes
    accident_detected = False

    if boxes is not None and len(boxes) > 0:
        # Direct numpy access — no pandas overhead
        data = boxes.data.cpu().numpy()

        for row in data:
            x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
            conf = row[4]
            cls_id = int(row[5])

            label = class_list[cls_id] if cls_id < len(class_list) else "Unknown"

            if label == "accident":
                accident_detected = True
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 4)
                cv2.putText(frame, f"ACCIDENT {conf:.0%}", (x1, y1 - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, f"{label} {conf:.0%}", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)

    if accident_detected:
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 65), (0, 0, 180), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        text = "!!! ACCIDENT DETECTED !!!"
        font = cv2.FONT_HERSHEY_DUPLEX
        (tw, th), _ = cv2.getTextSize(text, font, 1.1, 2)
        cx = (w - tw) // 2
        cv2.putText(frame, text, (cx + 2, 44), font, 1.1, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, (cx, 42), font, 1.1, (0, 255, 255), 2, cv2.LINE_AA)

        if int(time.time() * 3) % 2 == 0:
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 5)

        
        with accident_flag_lock:
            accident_flag = True

    return frame, accident_detected


def try_save_snapshot(frame):
    """Try to save a snapshot — only once per 30s, in background thread."""
    global last_snapshot_time
    now = time.time()
    if now - last_snapshot_time >= 30:
        last_snapshot_time = now
        snapshot = frame.copy()
        threading.Thread(target=save_snapshot_background, args=(snapshot,), daemon=True).start()


def generate_video_frames():
    """Generator for uploaded video streaming — OPTIMIZED."""
    global current_video_path, video_active

    if not current_video_path or not os.path.exists(current_video_path):
        return

    cap = cv2.VideoCapture(current_video_path)
    video_active = True
    frame_count = 0

    while video_active:
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            if not ret:
                break

        frame_count += 1
        if frame_count % 2 != 0:
            continue

        processed, detected = process_frame(frame)

       
        if detected:
            try_save_snapshot(processed)

        _, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    cap.release()
    video_active = False


def generate_camera_frames():
    """Generator for live camera streaming — OPTIMIZED."""
    global camera_active

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    camera_active = True
    frame_count = 0

    while camera_active:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        if frame_count % 2 != 0:
            continue

        processed, detected = process_frame(frame)

        
        if detected:
            try_save_snapshot(processed)

        _, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    cap.release()
    camera_active = False




@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for(get_post_login_endpoint()))

    error = None
    success = None
    show_signup = False

    if request.method == 'POST':
        form_type = request.form.get('form_type')
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if form_type == 'signup':
            show_signup = True
            confirm = request.form.get('confirm_password', '')
            role = request.form.get('role', 'admin').strip().lower()
            conn = get_db()

            if not email or not password:
                error = 'Please fill in all fields.'
            elif '@' not in email or '.' not in email:
                error = 'Please enter a valid email address.'
            elif len(password) < 4:
                error = 'Password must be at least 4 characters.'
            elif password != confirm:
                error = 'Passwords do not match.'
            elif role not in {'admin', 'responder'}:
                error = 'Please select a valid role.'
            else:
                existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
                if existing:
                    error = 'An account with this email already exists.'
                else:
                    conn.execute(
                        'INSERT INTO users (email, password, role) VALUES (?, ?, ?)',
                        (email, hash_password(password), role)
                    )
                    conn.commit()
                    success = 'Account created successfully! Please sign in.'
                    show_signup = False
            conn.close()

        elif form_type == 'login':
            conn = get_db()
            user = conn.execute(
                'SELECT * FROM users WHERE email = ? AND password = ?',
                (email, hash_password(password))
            ).fetchone()
            conn.close()

            if user:
                session['user_id'] = user['id']
                session['user_email'] = email
                session['user_role'] = user['role'] if user['role'] in {'admin', 'responder'} else 'admin'
                return redirect(url_for(get_post_login_endpoint()))
            else:
                error = 'Invalid email or password.'

    return render_template('login.html', error=error, success=success, show_signup=show_signup)


@app.route('/logout')
def logout():
    session.clear()
    response = redirect(url_for('intro'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/')
def intro():
    return render_template('intro.html')


@app.route('/home', endpoint='home')
@login_required
def home():
    return redirect(url_for(get_post_login_endpoint()))


@app.route('/dashboard', endpoint='dashboard')
@login_required
def dashboard():
    return render_dashboard_page()


@app.route('/detect')
@admin_required
def detect():
    return render_template('detect.html', **build_dashboard_context())


@app.route('/alerts')
@login_required
def alerts_page():
    conn = get_db()
    rows = conn.execute('SELECT * FROM accidents WHERE closed = 0 ORDER BY timestamp DESC').fetchall()
    alert_counts = get_alert_counts(conn)
    conn.close()

    accidents = [serialize_accident(row) for row in rows]
    context = build_dashboard_context()
    context.update(alert_counts)

    return render_template('alerts.html', accidents=accidents, **context)


@app.route('/contact_authority', methods=['POST'])
@login_required
def contact_authority():
    if get_current_role() != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    accident_id = data.get('id')

    conn = get_db()
    row = conn.execute(
        'SELECT notified, responded, closed, reported_at, responded_at FROM accidents WHERE id = ?',
        (accident_id,)
    ).fetchone()
    if row:
        if row['notified']:
            alert_counts = get_alert_counts(conn)
            conn.close()
            return jsonify({
                'success': True,
                'notified': True,
                'status': build_incident_status(True, bool(row['responded']), bool(row['closed'])),
                **alert_counts,
                'message': 'Authorities already contacted'
            })

        reported_at = time.time()
        conn.execute(
            'UPDATE accidents SET notified = 1, reported_at = ? WHERE id = ?',
            (reported_at, accident_id)
        )
        conn.commit()
        alert_counts = get_alert_counts(conn)
        conn.close()
        return jsonify({
            'success': True,
            'notified': True,
            'status': 'Active',
            **alert_counts,
            'message': 'Incident reported to authorities and marked active.'
        })

    conn.close()
    return jsonify({'success': False, 'error': 'Accident not found'}), 404


@app.route('/mark_responded', methods=['POST'])
@login_required
def mark_responded():
    if get_current_role() != 'responder':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    accident_id = data.get('id')

    conn = get_db()
    row = conn.execute(
        'SELECT notified, responded, closed, reported_at, responded_at FROM accidents WHERE id = ?',
        (accident_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    if not row['notified']:
        conn.close()
        return jsonify({'success': False, 'error': 'This alert has not been sent to responders yet'}), 400

    if row['closed']:
        alert_counts = get_alert_counts(conn)
        conn.close()
        return jsonify({
            'success': True,
            'status': 'Closed',
            'responded': True,
            'closed': True,
            **alert_counts,
            'message': 'Alert already closed'
        })

    if row['responded']:
        conn.execute('UPDATE accidents SET closed = 1 WHERE id = ?', (accident_id,))
        conn.commit()
        alert_counts = get_alert_counts(conn)
        conn.close()
        return jsonify({
            'success': True,
            'status': 'Closed',
            'responded': True,
            'closed': True,
            **alert_counts,
            'message': 'Incident closed and removed from active cases.'
        })

    responded_at = time.time()
    conn.execute(
        'UPDATE accidents SET responded = 1, responded_at = ? WHERE id = ?',
        (responded_at, accident_id)
    )
    conn.commit()
    alert_counts = get_alert_counts(conn)
    conn.close()
    return jsonify({
        'success': True,
        'status': 'Responded',
        'responded': True,
        'closed': False,
        **alert_counts,
        'message': 'Incident marked as responded. You can close it now.'
    })




@app.route('/upload', methods=['POST'])
@admin_required
def upload_video():
    global current_video_path, video_active

    video_active = False
    time.sleep(0.3)

    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: mp4, avi, mov, mkv, wmv, webm'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    current_video_path = filepath

    return jsonify({'success': True, 'filename': filename})


@app.route('/video_feed')
@admin_required
def video_feed():
    return Response(generate_video_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/camera_feed')
@admin_required
def camera_feed():
    return Response(generate_camera_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/process_camera_frame', methods=['POST'])
@admin_required
def process_camera_frame():
    try:
        frame = None

        if 'frame' in request.files:
            image_bytes = request.files['frame'].read()
        else:
            data = request.get_json(silent=True) or {}
            frame_data = data.get('frame') or data.get('image')

            if not frame_data:
                return jsonify({'success': False, 'error': 'No frame provided'}), 400

            if ',' in frame_data:
                frame_data = frame_data.split(',', 1)[1]

            image_bytes = base64.b64decode(frame_data)

        np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid frame data'}), 400

    if frame is None:
        return jsonify({'success': False, 'error': 'Unable to decode frame'}), 400

    try:
        processed, detected = process_frame(frame)

        if detected:
            try_save_snapshot(processed)

        ok, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        if not ok:
            return jsonify({'success': False, 'error': 'Unable to encode frame'}), 500

        encoded = base64.b64encode(buffer.tobytes()).decode('ascii')
        return jsonify({
            'success': True,
            'detected': detected,
            'image': f'data:image/jpeg;base64,{encoded}'
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Camera processing failed: {exc}'}), 500


@app.route('/stop_video', methods=['POST'])
@admin_required
def stop_video():
    global video_active
    video_active = False
    return jsonify({'success': True})


@app.route('/stop_camera', methods=['POST'])
@admin_required
def stop_camera():
    global camera_active
    camera_active = False
    return jsonify({'success': True})


@app.route('/accident_status')
@admin_required
def accident_status():
    """Polled by browser to trigger sound — returns True once then resets."""
    global accident_flag
    with accident_flag_lock:
        status = accident_flag
        accident_flag = False
    return jsonify({'accident': status})



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000,
            use_reloader=False, threaded=True)
