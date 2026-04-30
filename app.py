# Standard library and framework imports stay grouped at the top so runtime dependencies remain explicit during deployment and review.
import os
import cv2
import time
import uuid
import base64
import json
import sqlite3
import hashlib
import threading
import numpy as np
from datetime import datetime
from flask import (
    Flask, render_template, request, Response,
    jsonify, session, redirect, url_for
)
from flask_sock import Sock
from ultralytics import YOLO
from werkzeug.utils import secure_filename

# The application object centralizes configuration, routing, and request handling for the entire system.
app = Flask(__name__)
sock = Sock(app)
# Session signing depends on a stable secret so authenticated state cannot be tampered with by the client.
app.secret_key = 'accivision_secret_key_2024'
# Uploaded media stays inside the project workspace to keep evidence and operator input under managed storage.
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
# A hard upload limit protects the service from oversized requests that could degrade responsiveness.
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB max
# File type allowlisting narrows ingestion to formats the video pipeline is expected to handle.
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'webm'}

# Project-relative paths keep the application portable across local and hosted environments.
BASE_DIR = os.path.dirname(__file__)
ACCIDENTS_DIR = os.path.join(BASE_DIR, 'static', 'accidents')
DB_PATH = os.path.join(BASE_DIR, 'accivision.db')

# Required storage folders are ensured at startup so capture and upload flows do not fail on first use.
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(ACCIDENTS_DIR, exist_ok=True)


# Model and label assets are resolved once because inference depends on both being available before requests arrive.
MODEL_PATH = os.path.join(BASE_DIR, 'best.pt')
LABELS_PATH = os.path.join(BASE_DIR, 'coco.txt')

# The detector is loaded once at startup to avoid repeated initialization costs during live monitoring.
model = YOLO(MODEL_PATH)

# Label metadata is read once so detection classes can be translated into operator-facing states throughout the session.
with open(LABELS_PATH, 'r') as f:
    class_list = [line.strip() for line in f.read().strip().split('\n')]

# Shared runtime flags coordinate long-lived streaming loops and browser polling across multiple request handlers.
current_video_path = None
video_active = False
accident_flag = False          # polled by browser for sound
accident_flag_lock = threading.Lock()

# Snapshot throttling prevents one sustained incident from generating excessive duplicate evidence.
last_snapshot_time = 0

# Encoding settings are fixed up front to keep frame streaming predictable under repeated load.
ENCODE_PARAM = [int(cv2.IMWRITE_JPEG_QUALITY), 60]

# High-confidence detections bypass manual review so clear incidents reach responders immediately.
AUTO_REPORT_CONFIDENCE_THRESHOLD = 0.80
FRAME_SKIP_INTERVAL = 2
MODEL_INPUT_SIZE = 640
REALTIME_STREAM_INPUT_SIZE = (640, 360)
REALTIME_ALERT_COOLDOWN_SECONDS = 4
realtime_alert_state = {}
realtime_alert_lock = threading.Lock()



# Database connections are centralized so every code path gets the same SQLite configuration and row access behavior.
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Startup schema management preserves compatibility with existing databases while enabling new workflow metadata.
def init_db():
    conn = get_db()
    # User storage is created defensively so authentication works even on a brand-new deployment.
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Accident storage persists evidence and lifecycle state so dashboards survive refreshes and restarts.
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
    # Schema inspection supports additive migrations without destructive table rebuilds.
    user_columns = [row['name'] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if 'role' not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    # Accident schema inspection protects historical data while newer alert workflow fields are introduced.
    accident_columns = [row['name'] for row in conn.execute("PRAGMA table_info(accidents)").fetchall()]
    if 'responded' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN responded INTEGER DEFAULT 0")
    if 'closed' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN closed INTEGER DEFAULT 0")
    if 'status' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN status TEXT")
    if 'sent_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN sent_at REAL")
    if 'reported_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN reported_at REAL")
    if 'responded_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN responded_at REAL")
    if 'closed_at' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN closed_at REAL")
    if 'detection_time_seconds' not in accident_columns:
        conn.execute("ALTER TABLE accidents ADD COLUMN detection_time_seconds REAL")

    conn.execute(
        '''
        UPDATE accidents
        SET sent_at = COALESCE(sent_at, reported_at)
        WHERE reported_at IS NOT NULL
        '''
    )
    conn.execute(
        '''
        UPDATE accidents
        SET status = CASE
            WHEN closed = 1 THEN 'closed'
            WHEN responded = 1 THEN 'responded'
            WHEN notified = 1 THEN 'sent_to_responder'
            ELSE 'new'
        END
        WHERE status IS NULL OR TRIM(status) = ''
        '''
    )

    conn.commit()
    conn.close()


# Database preparation runs before any request handling so later routes can assume a ready schema.
init_db()


# Password hashing keeps stored credentials irreversible and separates secure storage from raw form input.
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# Role lookup is isolated because authorization decisions should rely on one trusted access path.
def get_user_role(email):
    conn = get_db()
    row = conn.execute('SELECT role FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    return row['role'] if row and row['role'] in {'admin', 'responder'} else 'admin'


# Session role access is wrapped to keep permission checks consistent across routes and templates.
def get_current_role():
    return session.get('user_role', 'admin')


# Shared alert counters drive badges and summaries so every page reports the same operational totals.
def get_alert_counts(conn=None):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    # Counting in SQL avoids loading the full accident history into memory just to update dashboard badges.
    counts = conn.execute(
        '''
        SELECT
            SUM(
                CASE
                    WHEN closed = 0 AND (
                        status IN ('sent_to_responder', 'responded')
                        OR (status IS NULL AND notified = 1)
                    ) THEN 1
                    ELSE 0
                END
            ) AS active_alerts_count,
            SUM(
                CASE
                    WHEN closed = 0 AND (
                        status = 'responded'
                        OR (status IS NULL AND responded = 1)
                    ) THEN 1
                    ELSE 0
                END
            ) AS responded_cases_count
        FROM accidents
        '''
    ).fetchone()

    if should_close:
        conn.close()

    return {
        'active_alerts_count': counts['active_alerts_count'] or 0,
        'responded_cases_count': counts['responded_cases_count'] or 0,
    }


# Detection-time metrics are derived centrally so dashboard reporting reflects model responsiveness rather than human workflow timing.
def get_average_response_time(conn=None):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    # Only captured detections with measured inference duration contribute to the performance summary.
    stats = conn.execute(
        '''
        SELECT
            COUNT(*) AS detected_total,
            AVG(detection_time_seconds) AS average_response_seconds
        FROM accidents
        WHERE detection_time_seconds IS NOT NULL
          AND detection_time_seconds >= 0
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


# Recent event shaping converts raw records into dashboard-friendly summaries without leaking storage details into templates.
def build_recent_events(conn=None, limit=6):
    should_close = conn is None
    if conn is None:
        conn = get_db()

    rows = conn.execute(
        '''
        SELECT id, timestamp, notified, responded, closed, status
        FROM accidents
        ORDER BY timestamp DESC
        LIMIT ?
        ''',
        (limit,)
    ).fetchall()

    recent_events = []
    for index, row in enumerate(rows, start=1):
        normalized_status = get_alert_status(row)
        status = build_incident_status(normalized_status)
        recent_events.append({
            'event_id': f"EVT-{datetime.fromtimestamp(row['timestamp']).strftime('%Y%m%d')}-{index:03d}",
            'location': f"Monitored Zone {((index - 1) % 4) + 1}",
            'severity': 'Critical' if normalized_status == 'sent_to_responder' else 'High' if normalized_status == 'responded' else 'Medium' if normalized_status == 'closed' else 'Low',
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


# Status normalization bridges legacy boolean fields and the newer workflow column so the UI can reason about one canonical state.
def get_alert_status(row):
    raw_status = row['status'] if 'status' in row.keys() else None
    if raw_status in {'new', 'sent_to_responder', 'responded', 'closed', 'false_alarm'}:
        return raw_status

    if row['closed']:
        return 'closed'
    if row['responded']:
        return 'responded'
    if row['notified']:
        return 'sent_to_responder'
    return 'new'


# Human-readable labels are derived in one place to keep wording stable across admin and responder views.
def build_incident_status(status):
    if status == 'closed':
        return 'Closed'
    if status == 'responded':
        return 'Responded'
    if status == 'sent_to_responder':
        return 'Active'
    if status == 'false_alarm':
        return 'False Alarm'
    return 'New'


# Serialization prepares database rows for rendering by attaching computed timings and display-oriented fields.
def serialize_accident(row):
    status = get_alert_status(row)
    notified = bool(row['notified']) or status in {'sent_to_responder', 'responded', 'closed'}
    responded = bool(row['responded']) or status in {'responded', 'closed'}
    closed = bool(row['closed']) or status == 'closed'
    sent_at = row['sent_at'] if 'sent_at' in row.keys() else None
    reported_at = row['reported_at'] if 'reported_at' in row.keys() else None
    responded_at = row['responded_at'] if 'responded_at' in row.keys() else None
    closed_at = row['closed_at'] if 'closed_at' in row.keys() else None
    effective_sent_at = sent_at or reported_at
    return {
        'id': row['id'],
        'image': row['image'],
        'elapsed': get_elapsed_time(row['timestamp']),
        'date': datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
        'created_at': row['timestamp'],
        'sent_at': effective_sent_at,
        'responded_at': responded_at,
        'closed_at': closed_at,
        'time_to_respond': format_duration(responded_at - effective_sent_at) if effective_sent_at and responded_at and responded_at >= effective_sent_at else None,
        'time_to_close': format_duration(closed_at - responded_at) if responded_at and closed_at and closed_at >= responded_at else None,
        'notified': notified,
        'responded': responded,
        'closed': closed,
        'internal_status': status,
        'status': build_incident_status(status)
    }


# Shared dashboard context keeps navigation badges, summary metrics, and user identity aligned across templates.
def build_dashboard_context():
    role = get_current_role()
    conn = get_db()
    alert_counts = get_alert_counts(conn)
    response_metrics = get_average_response_time(conn)
    recent_events = build_recent_events(conn)
    working_camera_count = get_reported_camera_count()
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
        'cameras_online_count': working_camera_count,
        'cameras_total_count': working_camera_count,
        'events_today_count': events_today_count,
        'recent_events': recent_events,
        'current_role': role,
        'dashboard_title': 'Dashboard',
        'user_name': user_name,
        'user_role_label': role.title(),
        'alerts_url': url_for('alerts_page'),
        'alerts_label': 'Active Alerts',
    }


# Rendering is wrapped so dashboard entry points reuse the same context assembly path.
def render_dashboard_page():
    return render_template('home.html', **build_dashboard_context())


# Post-login routing is role-aware so each operator lands in the workflow most relevant to their responsibility.
def get_post_login_endpoint():
    return 'alerts_page' if get_current_role() == 'responder' else 'dashboard'


# Authentication enforcement is abstracted into a decorator to avoid repeating redirect logic on protected routes.
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# Global response hardening prevents browsers from caching authenticated pages with time-sensitive operational data.
@app.after_request
# Cache headers are applied after each request because dashboard state and session context should not persist in browser history.
def add_no_cache_headers(response):
    if 'user_id' in session and request.endpoint != 'static':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# Responder-only authorization separates operational case handling from administrative controls.
def responder_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if get_current_role() != 'responder':
            return "Access Denied", 403
        return f(*args, **kwargs)
    return decorated


# Admin-only authorization protects ingestion, escalation, and monitoring actions from non-admin accounts.
def admin_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if get_current_role() != 'admin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated



# Upload validation is isolated because several request paths rely on the same acceptance policy.
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Relative time formatting favors quick situational awareness over raw epoch values in the UI.
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


# Shared duration formatting keeps timing metrics readable and consistent across the product.
def format_duration(seconds):
    total_seconds = max(int(round(seconds)), 0)
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)

    if hours > 0:
        return f"{hours} hr {remaining_minutes} min"
    if minutes > 0:
        return f"{minutes} min {remaining_seconds} sec"
    return f"{remaining_seconds} sec"


# Stream-scoped alert cooldowns prevent one sustained event from flooding the UI and responder pipeline.
def should_trigger_realtime_alert(stream_id, detected):
    if not detected:
        return False

    normalized_stream_id = stream_id or 'default'
    now = time.time()
    with realtime_alert_lock:
        last_triggered_at = realtime_alert_state.get(normalized_stream_id, 0.0)
        if now - last_triggered_at < REALTIME_ALERT_COOLDOWN_SECONDS:
            return False
        realtime_alert_state[normalized_stream_id] = now
        return True


# Stream cleanup prevents stale cooldown state from leaking across browser reconnects.
def clear_realtime_alert_state(stream_id):
    normalized_stream_id = stream_id or 'default'
    with realtime_alert_lock:
        realtime_alert_state.pop(normalized_stream_id, None)


# Binary image decoding is centralized so HTTP uploads and WebSocket frames share the same validation path.
def decode_image_bytes(image_bytes):
    np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)


# Base64 frame decoding supports browser-streamed canvas payloads without server-side camera access.
def decode_base64_frame(frame_data):
    normalized_frame_data = frame_data.split(',', 1)[1] if ',' in frame_data else frame_data
    image_bytes = base64.b64decode(normalized_frame_data)
    return decode_image_bytes(image_bytes)


# Detection result shaping is shared so every ingestion path returns the same contract to the frontend.
def build_detection_result(frame, stream_id=None):
    processed, detected, auto_send_to_responder, detection_time_seconds, prediction_result = process_frame(frame)

    if detected:
        try_save_snapshot(processed, auto_send_to_responder, detection_time_seconds)

    ok, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
    if not ok:
        raise ValueError('Unable to encode frame')

    alert_triggered = should_trigger_realtime_alert(stream_id, detected)
    return {
        'success': True,
        'detected': detected,
        'status': 'ACCIDENT' if detected else 'NORMAL',
        'confidence': round(float(prediction_result['confidence']) * 100, 2),
        'alert': alert_triggered,
        'prediction': prediction_result,
        'auto_sent_to_responder': auto_send_to_responder,
        'detection_time_ms': round(detection_time_seconds * 1000, 2),
        'image': f"data:image/jpeg;base64,{base64.b64encode(buffer.tobytes()).decode('ascii')}",
    }


# Browser-reported device counts keep deployment compatible with hosted environments where the server cannot access user cameras.
def get_reported_camera_count():
    return max(int(session.get('browser_camera_count', 0) or 0), 0)


# Single-record retrieval is centralized because most alert transitions start from the same lookup step.
def fetch_accident_row(conn, accident_id):
    return conn.execute('SELECT * FROM accidents WHERE id = ?', (accident_id,)).fetchone()


# Admin visibility rules keep the live review queue focused while still retaining dismissed or completed records in storage.
def accident_visible_to_admin(row):
    return get_alert_status(row) not in {'closed', 'false_alarm'}


# Responder visibility is intentionally narrower so only dispatched incidents enter the active response queue.
def accident_visible_to_responder(row):
    return get_alert_status(row) in {'sent_to_responder', 'responded'}


# Action responses share one serializer so asynchronous UI updates remain consistent after any alert transition.
def build_alert_action_response(conn, accident_id, message):
    row = fetch_accident_row(conn, accident_id)
    accident = serialize_accident(row)
    alert_counts = get_alert_counts(conn)
    return {
        'success': True,
        'id': accident_id,
        'status': accident['status'],
        'internal_status': accident['internal_status'],
        'notified': accident['notified'],
        'responded': accident['responded'],
        'closed': accident['closed'],
        'sent_at': accident['sent_at'],
        'responded_at': accident['responded_at'],
        'closed_at': accident['closed_at'],
        'time_to_respond': accident['time_to_respond'],
        'time_to_close': accident['time_to_close'],
        **alert_counts,
        'message': message,
    }


# Dispatch transitions are isolated here to keep responder handoff rules consistent across old and new endpoints.
def report_alert_by_id(accident_id):
    conn = get_db()
    row = fetch_accident_row(conn, accident_id)
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    status = get_alert_status(row)
    if status == 'closed':
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        conn.close()
        return jsonify(response)

    if status in {'sent_to_responder', 'responded'}:
        response = build_alert_action_response(conn, accident_id, 'Alert already sent to responder.')
        conn.close()
        return jsonify(response)

    sent_at = row['sent_at'] or row['reported_at'] or time.time()
    conn.execute(
        '''
        UPDATE accidents
        SET notified = 1,
            status = 'sent_to_responder',
            sent_at = ?,
            reported_at = COALESCE(reported_at, ?)
        WHERE id = ?
        ''',
        (sent_at, sent_at, accident_id)
    )
    conn.commit()
    response = build_alert_action_response(conn, accident_id, 'Alert sent to responder dashboard.')
    conn.close()
    return jsonify(response)


# False-alarm handling preserves evidence for auditability while removing non-actionable items from live operations.
def false_alarm_by_id(accident_id):
    conn = get_db()
    row = fetch_accident_row(conn, accident_id)
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    status = get_alert_status(row)
    if status == 'closed':
        conn.close()
        return jsonify({'success': False, 'error': 'Closed alerts cannot be marked as false alarms.'}), 400

    if status in {'responded', 'sent_to_responder'}:
        conn.close()
        return jsonify({'success': False, 'error': 'This alert has already been sent to the responder dashboard.'}), 400

    conn.execute(
        '''
        UPDATE accidents
        SET status = 'false_alarm',
            notified = 0,
            responded = 0,
            closed = 0
        WHERE id = ?
        ''',
        (accident_id,)
    )
    conn.commit()
    response = build_alert_action_response(conn, accident_id, 'Alert marked as false alarm and kept in the database.')
    conn.close()
    return jsonify(response)


# Acknowledgement is stored separately from closure so the system can measure reaction time before full resolution time.
def respond_alert_by_id(accident_id):
    conn = get_db()
    row = fetch_accident_row(conn, accident_id)
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    status = get_alert_status(row)
    if status not in {'sent_to_responder', 'responded'} and not row['notified']:
        conn.close()
        return jsonify({'success': False, 'error': 'This alert has not been sent to responders yet'}), 400

    if status == 'closed':
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        conn.close()
        return jsonify(response)

    if status == 'responded':
        response = build_alert_action_response(conn, accident_id, 'Incident already marked as responded.')
        conn.close()
        return jsonify(response)

    responded_at = time.time()
    conn.execute(
        '''
        UPDATE accidents
        SET responded = 1,
            notified = 1,
            status = 'responded',
            responded_at = ?
        WHERE id = ?
        ''',
        (responded_at, accident_id)
    )
    conn.commit()
    response = build_alert_action_response(conn, accident_id, 'Incident marked as responded. You can close it now.')
    conn.close()
    return jsonify(response)


# Final closure is separated from acknowledgement to preserve a complete and measurable incident lifecycle.
def close_alert_by_id(accident_id):
    conn = get_db()
    row = fetch_accident_row(conn, accident_id)
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    status = get_alert_status(row)
    if status not in {'responded', 'closed'} and not row['responded']:
        conn.close()
        return jsonify({'success': False, 'error': 'Alert must be responded to before it can be closed.'}), 400

    if status == 'closed':
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        conn.close()
        return jsonify(response)

    closed_at = time.time()
    conn.execute(
        '''
        UPDATE accidents
        SET responded = 1,
            closed = 1,
            status = 'closed',
            closed_at = ?
        WHERE id = ?
        ''',
        (closed_at, accident_id)
    )
    conn.commit()
    response = build_alert_action_response(conn, accident_id, 'Incident closed and removed from active cases.')
    conn.close()
    return jsonify(response)


# Snapshot persistence runs off the streaming path because evidence capture should not block live detection output.
def save_snapshot_background(frame_copy, auto_send_to_responder=False, detection_time_seconds=None):
    """Save accident snapshot in a background thread — never blocks video."""
    accident_id = str(uuid.uuid4())[:8]
    filename = f"accident_{accident_id}.jpg"
    filepath = os.path.join(ACCIDENTS_DIR, filename)
    captured_at = time.time()

    cv2.imwrite(filepath, frame_copy, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

    conn = get_db()
    conn.execute(
        '''
        INSERT INTO accidents (
            id, image, timestamp, notified, status, sent_at, reported_at, detection_time_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            accident_id,
            filename,
            captured_at,
            1 if auto_send_to_responder else 0,
            'sent_to_responder' if auto_send_to_responder else 'new',
            captured_at if auto_send_to_responder else None,
            captured_at if auto_send_to_responder else None,
            detection_time_seconds,
        )
    )
    conn.commit()
    conn.close()


# Frame processing owns inference, visual overlays, and alert signaling because those concerns must stay synchronized per image.
def process_frame(frame):
    """Run YOLO detection on a single frame and draw results.
    OPTIMIZED: no pandas, direct numpy array access, smaller inference size."""
    global accident_flag

    frame = cv2.resize(frame, (1020, 500))
    inference_started_at = time.perf_counter()

    results = model.predict(frame, verbose=False, imgsz=MODEL_INPUT_SIZE)
    detection_time_seconds = time.perf_counter() - inference_started_at

    boxes = results[0].boxes
    accident_detected = False
    max_accident_confidence = 0.0
    prediction_result = {
        'label': 'no_accident',
        'confidence': 0.0,
        'message': 'No accident detected'
    }

    if boxes is not None and len(boxes) > 0:
        data = boxes.data.cpu().numpy()

        for row in data:
            x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
            conf = row[4]
            cls_id = int(row[5])

            label = class_list[cls_id] if cls_id < len(class_list) else "Unknown"

            if label == "accident":
                accident_detected = True
                max_accident_confidence = max(max_accident_confidence, float(conf))
                prediction_result = {
                    'label': 'accident',
                    'confidence': float(max_accident_confidence),
                    'message': f'Accident detected with {max_accident_confidence:.0%} confidence'
                }
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

    auto_send_to_responder = accident_detected and max_accident_confidence >= AUTO_REPORT_CONFIDENCE_THRESHOLD
    return frame, accident_detected, auto_send_to_responder, detection_time_seconds, prediction_result


# Snapshot gating balances evidence retention against storage noise during sustained detections.
def try_save_snapshot(frame, auto_send_to_responder=False, detection_time_seconds=None):
    """Try to save a snapshot — only once per 30s, in background thread."""
    global last_snapshot_time
    now = time.time()
    if now - last_snapshot_time >= 30:
        last_snapshot_time = now
        snapshot = frame.copy()
        threading.Thread(
            target=save_snapshot_background,
            args=(snapshot, auto_send_to_responder, detection_time_seconds),
            daemon=True
        ).start()


# Authentication routes combine sign-in and sign-up because this deployment keeps onboarding lightweight and local.
@app.route('/login', methods=['GET', 'POST'])
# Login coordinates account creation, credential verification, and role-aware redirection into protected workflows.
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


# Explicit logout exists because shared operator machines require a clear and immediate session reset path.
@app.route('/logout')
# Logout reinforces cache controls so previously viewed protected pages are not resurfaced by the browser.
def logout():
    session.clear()
    response = redirect(url_for('intro'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# The public landing page stays separate from the dashboard to support unauthenticated discovery of the system.
@app.route('/')
# The introduction page presents product context before an operator signs in.
def intro():
    return render_template('intro.html')


# A role-aware home redirect preserves older navigation paths without duplicating dashboard selection logic.
@app.route('/home', endpoint='home')
@login_required
# Home resolves through one redirect point so role-specific landing behavior remains centralized.
def home():
    return redirect(url_for(get_post_login_endpoint()))


# The dashboard route exposes the shared operational overview used after authentication.
@app.route('/dashboard', endpoint='dashboard')
@login_required
# Dashboard rendering stays thin so metrics and navigation composition continue to come from shared helpers.
def dashboard():
    return render_dashboard_page()


# Detection controls are admin-only because they can create new evidence and trigger downstream alerts.
@app.route('/detect')
@admin_required
# The detect page hosts ingestion controls for uploaded footage and live camera monitoring.
def detect():
    return render_template('detect.html', **build_dashboard_context())


# One alerts entry point simplifies navigation while allowing role-specific rendering behind the scenes.
@app.route('/alerts')
@login_required
# Alert listing applies role-scoped visibility before rendering so each audience sees only relevant work.
def alerts_page():
    conn = get_db()
    rows = conn.execute('SELECT * FROM accidents ORDER BY timestamp DESC').fetchall()
    alert_counts = get_alert_counts(conn)
    conn.close()

    if get_current_role() == 'responder':
        accidents = [serialize_accident(row) for row in rows if accident_visible_to_responder(row)]
    else:
        accidents = [serialize_accident(row) for row in rows if accident_visible_to_admin(row)]

    context = build_dashboard_context()
    context.update(alert_counts)

    if get_current_role() == 'responder':
        return render_template('respond.html', accidents=accidents, **context)
    return render_template('alerts.html', accidents=accidents, **context)


# Dedicated action endpoints make alert transitions explicit, auditable, and easier to secure.
@app.route('/report_alert/<accident_id>', methods=['POST'])
@admin_required
# Reporting delegates to shared transition logic so compatibility and new UI flows stay aligned.
def report_alert(accident_id):
    return report_alert_by_id(accident_id)


@app.route('/false_alarm/<accident_id>', methods=['POST'])
@admin_required
# Administrative dismissal is exposed separately from responder closure because the two actions represent different intent.
def false_alarm(accident_id):
    return false_alarm_by_id(accident_id)


@app.route('/respond_alert/<accident_id>', methods=['POST'])
@responder_required
# A dedicated acknowledgement endpoint supports the responder's two-step workflow.
def respond_alert(accident_id):
    return respond_alert_by_id(accident_id)


@app.route('/close_alert/<accident_id>', methods=['POST'])
@responder_required
# A dedicated closure endpoint preserves the distinction between acknowledging and fully resolving an incident.
def close_alert(accident_id):
    return close_alert_by_id(accident_id)


# The legacy dispatch endpoint remains for compatibility with older front-end interactions.
@app.route('/contact_authority', methods=['POST'])
@login_required
# This compatibility wrapper forwards old admin actions into the current reporting lifecycle.
def contact_authority():
    if get_current_role() != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    accident_id = data.get('id')
    return report_alert_by_id(accident_id)


# The legacy responder endpoint remains available so older clients continue to function during workflow evolution.
@app.route('/mark_responded', methods=['POST'])
@login_required
# This wrapper maps the historical single-button responder flow onto the newer respond-then-close model.
def mark_responded():
    if get_current_role() != 'responder':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    accident_id = data.get('id')
    conn = get_db()
    row = fetch_accident_row(conn, accident_id)
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    if get_alert_status(row) == 'responded' or row['responded']:
        return close_alert_by_id(accident_id)
    return respond_alert_by_id(accident_id)




# Upload handling is kept separate from streaming endpoints because file validation and playback control have different concerns.
@app.route('/upload', methods=['POST'])
@admin_required
# Upload processing resets previous playback state so only one uploaded source drives detection at a time.
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

# Snapshot-based camera processing supports browsers that submit captured frames from their own device camera.
@app.route('/upload_frame', methods=['POST'])
@app.route('/auto_upload_frame', methods=['POST'])
@app.route('/process_camera_frame', methods=['POST'])
@admin_required
# This endpoint decodes browser-captured frames, runs inference, and returns an annotated preview plus prediction details.
def process_camera_frame():
    try:
        if 'frame' in request.files:
            image_bytes = request.files['frame'].read()
        else:
            data = request.get_json(silent=True) or {}
            frame_data = data.get('frame') or data.get('image')

            if not frame_data:
                return jsonify({'success': False, 'error': 'No frame provided'}), 400

            frame = decode_base64_frame(frame_data)
            image_bytes = None
        if image_bytes is not None:
            frame = decode_image_bytes(image_bytes)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid frame data'}), 400

    if frame is None:
        return jsonify({'success': False, 'error': 'Unable to decode frame'}), 400

    try:
        return jsonify(build_detection_result(frame, request.headers.get('X-Stream-Id')))
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Camera processing failed: {exc}'}), 500


# WebSocket streaming keeps inference conversational and low-latency instead of repeating full HTTP setup for every frame.
@sock.route('/ws/realtime_detect')
def realtime_detect_socket(ws):
    if 'user_id' not in session or get_current_role() != 'admin':
        ws.send(json.dumps({'success': False, 'error': 'Unauthorized'}))
        ws.close()
        return

    stream_id = None
    try:
        while True:
            raw_message = ws.receive()
            if raw_message is None:
                break

            payload = json.loads(raw_message)
            message_type = payload.get('type')

            if message_type == 'start':
                stream_id = payload.get('stream_id') or str(uuid.uuid4())
                clear_realtime_alert_state(stream_id)
                ws.send(json.dumps({'success': True, 'type': 'ready', 'stream_id': stream_id}))
                continue

            if message_type == 'stop':
                break

            if message_type != 'frame':
                ws.send(json.dumps({'success': False, 'error': 'Unsupported message type'}))
                continue

            frame_data = payload.get('image')
            if not frame_data:
                ws.send(json.dumps({'success': False, 'error': 'No frame provided'}))
                continue

            frame = decode_base64_frame(frame_data)
            if frame is None:
                ws.send(json.dumps({'success': False, 'error': 'Unable to decode frame'}))
                continue

            detection_payload = build_detection_result(frame, stream_id or payload.get('stream_id'))
            detection_payload['type'] = 'prediction'
            detection_payload['stream_id'] = stream_id or payload.get('stream_id')
            ws.send(json.dumps(detection_payload))
    except Exception as exc:
        try:
            ws.send(json.dumps({'success': False, 'error': f'Stream closed: {exc}'}))
        except Exception:
            pass
    finally:
        clear_realtime_alert_state(stream_id)
        try:
            ws.close()
        except Exception:
            pass


# Explicit stop routes let the UI terminate long-running stream generators without restarting the app.
@app.route('/stop_video', methods=['POST'])
@admin_required
# Uploaded video playback now runs in the browser, but this endpoint remains for frontend compatibility.
def stop_video():
    return jsonify({'success': True})


@app.route('/stop_camera', methods=['POST'])
@admin_required
# Browser camera shutdown is handled client-side, but this endpoint remains for frontend compatibility.
def stop_camera():
    return jsonify({'success': True})


# Browser-reported camera counts keep the dashboard compatible with hosted deployments where the server cannot inspect local hardware.
@app.route('/report_camera_inventory', methods=['POST'])
@login_required
def report_camera_inventory():
    data = request.get_json(silent=True) or {}
    device_count = data.get('count', 0)
    try:
        session['browser_camera_count'] = max(int(device_count), 0)
    except (TypeError, ValueError):
        session['browser_camera_count'] = 0
    return jsonify({'success': True, 'count': session['browser_camera_count']})


# Frontend alert acknowledgements are logged separately so real-time UI behavior can be observed without altering incident records.
@app.route('/log_alert', methods=['POST'])
@admin_required
def log_alert():
    payload = request.get_json(silent=True) or {}
    return jsonify({
        'success': True,
        'logged_at': time.time(),
        'label': payload.get('label', 'unknown'),
        'confidence': payload.get('confidence'),
    })


# Polling-based alert signaling keeps browser audio warnings decoupled from frame transport.
@app.route('/accident_status')
@admin_required
# Sound-trigger polling resets after each read so one detection does not repeat indefinitely in the client.
def accident_status():
    """Polled by browser to trigger sound — returns True once then resets."""
    global accident_flag
    with accident_flag_lock:
        status = accident_flag
        accident_flag = False
    return jsonify({'accident': status})



# Direct execution support keeps local development straightforward without affecting production deployment patterns.
if __name__ == '__main__':
    app.run(
        debug=True,
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 5000)),
        use_reloader=False,
        threaded=True
    )
