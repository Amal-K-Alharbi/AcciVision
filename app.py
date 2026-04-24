# Import os so this file can use it later.
import os
# Import cv2 so this file can use it later.
import cv2
# Import time so this file can use it later.
import time
# Import uuid so this file can use it later.
import uuid
# Import base64 so this file can use it later.
import base64
# Import sqlite3 so this file can use it later.
import sqlite3
# Import hashlib so this file can use it later.
import hashlib
# Import threading so this file can use it later.
import threading
# Import numpy as np so this file can use it later.
import numpy as np
# Import specific tools from another module so they can be used directly here.
from datetime import datetime
# Import specific tools from another module so they can be used directly here.
from flask import (
    # This line performs the next step of the current logic.
    Flask, render_template, request, Response,
    # This line performs the next step of the current logic.
    jsonify, session, redirect, url_for
# This line performs the next step of the current logic.
)
# Import specific tools from another module so they can be used directly here.
from ultralytics import YOLO
# Import specific tools from another module so they can be used directly here.
from werkzeug.utils import secure_filename

# Create the Flask application object that powers the web app.
app = Flask(__name__)
# Set the secret key Flask uses to protect session data.
app.secret_key = 'accivision_secret_key_2024'
# Save this application setting so Flask can use it later.
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
# Save this application setting so Flask can use it later.
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB max
# Store a value in ALLOWED_EXTENSIONS so it can be used later in the program.
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'webm'}

# Store a value in BASE_DIR so it can be used later in the program.
BASE_DIR = os.path.dirname(__file__)
# Store a value in ACCIDENTS_DIR so it can be used later in the program.
ACCIDENTS_DIR = os.path.join(BASE_DIR, 'static', 'accidents')
# Store a value in DB_PATH so it can be used later in the program.
DB_PATH = os.path.join(BASE_DIR, 'accivision.db')

# Create this folder if it does not already exist.
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
# Create this folder if it does not already exist.
os.makedirs(ACCIDENTS_DIR, exist_ok=True)

# This existing comment notes: ─── Load Model & Labels ─────────────────────────────────────
# ─── Load Model & Labels ─────────────────────────────────────
# Store a value in MODEL_PATH so it can be used later in the program.
MODEL_PATH = os.path.join(BASE_DIR, 'best.pt')
# Store a value in LABELS_PATH so it can be used later in the program.
LABELS_PATH = os.path.join(BASE_DIR, 'coco.txt')

# Load the trained model so it can be used for accident detection.
model = YOLO(MODEL_PATH)

# Open and manage this resource safely, then clean it up automatically when the block ends.
with open(LABELS_PATH, 'r') as f:
    # Store a value in class_list so it can be used later in the program.
    class_list = [line.strip() for line in f.read().strip().split('\n')]

# This existing comment notes: ─── Global State ─────────────────────────────────────────────
# ─── Global State ─────────────────────────────────────────────
# Store a value in camera_active so it can be used later in the program.
camera_active = False
# Store a value in current_video_path so it can be used later in the program.
current_video_path = None
# Store a value in video_active so it can be used later in the program.
video_active = False
# Store a value in accident_flag so it can be used later in the program.
accident_flag = False          # polled by browser for sound
# Store a value in accident_flag_lock so it can be used later in the program.
accident_flag_lock = threading.Lock()

# This existing comment notes: Snapshot cooldown: save max 1 screenshot per 30 seconds
# Snapshot cooldown: save max 1 screenshot per 30 seconds
# Store a value in last_snapshot_time so it can be used later in the program.
last_snapshot_time = 0

# This existing comment notes: Pre-compute JPEG encode params once
# Pre-compute JPEG encode params once
# Store a value in ENCODE_PARAM so it can be used later in the program.
ENCODE_PARAM = [int(cv2.IMWRITE_JPEG_QUALITY), 60]


# This existing comment notes: ─── Database ─────────────────────────────────────────────────
# ─── Database ─────────────────────────────────────────────────

# Define the function get_db so this reusable block of code can be called later.
def get_db():
    # Store a value in conn so it can be used later in the program.
    conn = sqlite3.connect(DB_PATH)
    # Store a value in conn.row_factory so it can be used later in the program.
    conn.row_factory = sqlite3.Row
    # Return this value so the function sends a result back to its caller.
    return conn


# Define the function init_db so this reusable block of code can be called later.
def init_db():
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Run this SQL command on the SQLite database connection.
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        # This line performs the next step of the current logic.
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        # This line performs the next step of the current logic.
        email TEXT UNIQUE NOT NULL,
        # Do nothing here on purpose.
        password TEXT NOT NULL,
        # This line performs the next step of the current logic.
        role TEXT NOT NULL DEFAULT 'admin',
        # This line performs the next step of the current logic.
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    # This line performs the next step of the current logic.
    )''')
    # Run this SQL command on the SQLite database connection.
    conn.execute('''CREATE TABLE IF NOT EXISTS accidents (
        # This line performs the next step of the current logic.
        id TEXT PRIMARY KEY,
        # This line performs the next step of the current logic.
        image TEXT NOT NULL,
        # This line performs the next step of the current logic.
        timestamp REAL NOT NULL,
        # This line performs the next step of the current logic.
        notified INTEGER DEFAULT 0,
        # This line performs the next step of the current logic.
        responded INTEGER DEFAULT 0,
        # This line performs the next step of the current logic.
        closed INTEGER DEFAULT 0,
        # This line performs the next step of the current logic.
        reported_at REAL,
        # This line performs the next step of the current logic.
        responded_at REAL
    # This line performs the next step of the current logic.
    )''')
    # Store a value in user_columns so it can be used later in the program.
    user_columns = [row['name'] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    # Check this condition so the code only runs when the required case is true.
    if 'role' not in user_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    # Store a value in accident_columns so it can be used later in the program.
    accident_columns = [row['name'] for row in conn.execute("PRAGMA table_info(accidents)").fetchall()]
    # Check this condition so the code only runs when the required case is true.
    if 'responded' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN responded INTEGER DEFAULT 0")
    # Check this condition so the code only runs when the required case is true.
    if 'closed' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN closed INTEGER DEFAULT 0")
    # Check this condition so the code only runs when the required case is true.
    if 'status' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN status TEXT")
    # Check this condition so the code only runs when the required case is true.
    if 'sent_at' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN sent_at REAL")
    # Check this condition so the code only runs when the required case is true.
    if 'reported_at' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN reported_at REAL")
    # Check this condition so the code only runs when the required case is true.
    if 'responded_at' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN responded_at REAL")
    # Check this condition so the code only runs when the required case is true.
    if 'closed_at' not in accident_columns:
        # Run this SQL command on the SQLite database connection.
        conn.execute("ALTER TABLE accidents ADD COLUMN closed_at REAL")

    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET sent_at so it can be used later in the program.
        SET sent_at = COALESCE(sent_at, reported_at)
        # This line performs the next step of the current logic.
        WHERE reported_at IS NOT NULL
        # This line performs the next step of the current logic.
        '''
    # This line performs the next step of the current logic.
    )
    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET status so it can be used later in the program.
        SET status = CASE
            # Store a value in WHEN closed so it can be used later in the program.
            WHEN closed = 1 THEN 'closed'
            # Store a value in WHEN responded so it can be used later in the program.
            WHEN responded = 1 THEN 'responded'
            # Store a value in WHEN notified so it can be used later in the program.
            WHEN notified = 1 THEN 'sent_to_responder'
            # This line performs the next step of the current logic.
            ELSE 'new'
        # This line performs the next step of the current logic.
        END
        # Store a value in WHERE status IS NULL OR TRIM(status) so it can be used later in the program.
        WHERE status IS NULL OR TRIM(status) = ''
        # This line performs the next step of the current logic.
        '''
    # This line performs the next step of the current logic.
    )

    # Save the database changes permanently.
    conn.commit()
    # Close the database connection to free resources.
    conn.close()


# This line performs the next step of the current logic.
init_db()


# Define the function hash_password so this reusable block of code can be called later.
def hash_password(password):
    # Return this value so the function sends a result back to its caller.
    return hashlib.sha256(password.encode()).hexdigest()


# Define the function get_user_role so this reusable block of code can be called later.
def get_user_role(email):
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = conn.execute('SELECT role FROM users WHERE email = ?', (email,)).fetchone()
    # Close the database connection to free resources.
    conn.close()
    # Return this value so the function sends a result back to its caller.
    return row['role'] if row and row['role'] in {'admin', 'responder'} else 'admin'


# Define the function get_current_role so this reusable block of code can be called later.
def get_current_role():
    # Return this value so the function sends a result back to its caller.
    return session.get('user_role', 'admin')


# Define the function get_alert_counts so this reusable block of code can be called later.
def get_alert_counts(conn=None):
    # Store a value in should_close so it can be used later in the program.
    should_close = conn is None
    # Check this condition so the code only runs when the required case is true.
    if conn is None:
        # Store a value in conn so it can be used later in the program.
        conn = get_db()

    # Store the alert count query result so the totals can be read below.
    counts = conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        SELECT
            # This line performs the next step of the current logic.
            SUM(
                # This line performs the next step of the current logic.
                CASE
                    # Store a value in WHEN closed so it can be used later in the program.
                    WHEN closed = 0 AND (
                        # This line performs the next step of the current logic.
                        status IN ('sent_to_responder', 'responded')
                        # Store a value in OR (status IS NULL AND notified so it can be used later in the program.
                        OR (status IS NULL AND notified = 1)
                    # This line performs the next step of the current logic.
                    ) THEN 1
                    # This line performs the next step of the current logic.
                    ELSE 0
                # This line performs the next step of the current logic.
                END
            # This line performs the next step of the current logic.
            ) AS active_alerts_count,
            # This line performs the next step of the current logic.
            SUM(
                # This line performs the next step of the current logic.
                CASE
                    # Store a value in WHEN closed so it can be used later in the program.
                    WHEN closed = 0 AND (
                        # Store a value in status so it can be used later in the program.
                        status = 'responded'
                        # Store a value in OR (status IS NULL AND responded so it can be used later in the program.
                        OR (status IS NULL AND responded = 1)
                    # This line performs the next step of the current logic.
                    ) THEN 1
                    # This line performs the next step of the current logic.
                    ELSE 0
                # This line performs the next step of the current logic.
                END
            # This line performs the next step of the current logic.
            ) AS responded_cases_count
        # This line performs the next step of the current logic.
        FROM accidents
        # This line performs the next step of the current logic.
        '''
    # This line performs the next step of the current logic.
    ).fetchone()

    # Check this condition so the code only runs when the required case is true.
    if should_close:
        # Close the database connection to free resources.
        conn.close()

    # Return this value so the function sends a result back to its caller.
    return {
        # This line performs the next step of the current logic.
        'active_alerts_count': counts['active_alerts_count'] or 0,
        # This line performs the next step of the current logic.
        'responded_cases_count': counts['responded_cases_count'] or 0,
    # This line performs the next step of the current logic.
    }


# Define the function get_average_response_time so this reusable block of code can be called later.
def get_average_response_time(conn=None):
    # Store a value in should_close so it can be used later in the program.
    should_close = conn is None
    # Check this condition so the code only runs when the required case is true.
    if conn is None:
        # Store a value in conn so it can be used later in the program.
        conn = get_db()

    # Store the calculated statistics so this function can format them next.
    stats = conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        SELECT
            # This line performs the next step of the current logic.
            COUNT(*) AS responded_total,
            # This line performs the next step of the current logic.
            AVG(responded_at - COALESCE(sent_at, reported_at)) AS average_response_seconds
        # This line performs the next step of the current logic.
        FROM accidents
        # This line performs the next step of the current logic.
        WHERE COALESCE(sent_at, reported_at) IS NOT NULL
          # This line performs the next step of the current logic.
          AND responded_at IS NOT NULL
          # Store a value in AND responded_at > so it can be used later in the program.
          AND responded_at >= COALESCE(sent_at, reported_at)
        # This line performs the next step of the current logic.
        '''
    # This line performs the next step of the current logic.
    ).fetchone()

    # Check this condition so the code only runs when the required case is true.
    if should_close:
        # Close the database connection to free resources.
        conn.close()

    # Store a value in average_seconds so it can be used later in the program.
    average_seconds = stats['average_response_seconds']
    # Check this condition so the code only runs when the required case is true.
    if average_seconds is None:
        # Return this value so the function sends a result back to its caller.
        return {
            # This line performs the next step of the current logic.
            'avg_response_seconds': None,
            # This line performs the next step of the current logic.
            'avg_response_time_label': 'No data available'
        # This line performs the next step of the current logic.
        }

    # Return this value so the function sends a result back to its caller.
    return {
        # This line performs the next step of the current logic.
        'avg_response_seconds': average_seconds,
        # This line performs the next step of the current logic.
        'avg_response_time_label': format_duration(average_seconds)
    # This line performs the next step of the current logic.
    }


# Define the function get_active_alerts_count so this reusable block of code can be called later.
def get_active_alerts_count(conn=None):
    # Return this value so the function sends a result back to its caller.
    return get_alert_counts(conn)['active_alerts_count']


# Define the function get_responded_cases_count so this reusable block of code can be called later.
def get_responded_cases_count(conn=None):
    # Return this value so the function sends a result back to its caller.
    return get_alert_counts(conn)['responded_cases_count']


# Define the function build_recent_events so this reusable block of code can be called later.
def build_recent_events(conn=None, limit=6):
    # Store a value in should_close so it can be used later in the program.
    should_close = conn is None
    # Check this condition so the code only runs when the required case is true.
    if conn is None:
        # Store a value in conn so it can be used later in the program.
        conn = get_db()

    # Store the rows returned by this query so they can be used later.
    rows = conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        SELECT id, timestamp, notified, responded, closed, status
        # This line performs the next step of the current logic.
        FROM accidents
        # This line performs the next step of the current logic.
        ORDER BY timestamp DESC
        # This line performs the next step of the current logic.
        LIMIT ?
        # This line performs the next step of the current logic.
        ''',
        # Continue building the value from the previous line.
        (limit,)
    # This line performs the next step of the current logic.
    ).fetchall()

    # Store a value in recent_events so it can be used later in the program.
    recent_events = []
    # Loop through these items one by one so the same steps can be repeated for each item.
    for index, row in enumerate(rows, start=1):
        # Store a value in normalized_status so it can be used later in the program.
        normalized_status = get_alert_status(row)
        # Store a value in status so it can be used later in the program.
        status = build_incident_status(normalized_status)
        # This line performs the next step of the current logic.
        recent_events.append({
            # This line performs the next step of the current logic.
            'event_id': f"EVT-{datetime.fromtimestamp(row['timestamp']).strftime('%Y%m%d')}-{index:03d}",
            # This line performs the next step of the current logic.
            'location': f"Monitored Zone {((index - 1) % 4) + 1}",
            # This line performs the next step of the current logic.
            'severity': 'Critical' if normalized_status == 'sent_to_responder' else 'High' if normalized_status == 'responded' else 'Medium' if normalized_status == 'closed' else 'Low',
            # This line performs the next step of the current logic.
            'status': status,
            # This line performs the next step of the current logic.
            'time': get_elapsed_time(row['timestamp']),
        # This line performs the next step of the current logic.
        })

    # Check this condition so the code only runs when the required case is true.
    if should_close:
        # Close the database connection to free resources.
        conn.close()

    # Check this condition so the code only runs when the required case is true.
    if recent_events:
        # Return this value so the function sends a result back to its caller.
        return recent_events

    # Return this value so the function sends a result back to its caller.
    return [
        # Continue building the value from the previous line.
        {'event_id': 'EVT-DEMO-001', 'location': 'North Corridor Camera 04', 'severity': 'Critical', 'status': 'Active', 'time': '2 min ago'},
        # Continue building the value from the previous line.
        {'event_id': 'EVT-DEMO-002', 'location': 'Main St & 4th Ave', 'severity': 'High', 'status': 'Responded', 'time': '14 min ago'},
        # Continue building the value from the previous line.
        {'event_id': 'EVT-DEMO-003', 'location': 'Downtown Sector 2', 'severity': 'Medium', 'status': 'Closed', 'time': '38 min ago'},
    # This line performs the next step of the current logic.
    ]


# Define the function get_alert_status so this reusable block of code can be called later.
def get_alert_status(row):
    # Store a value in raw_status so it can be used later in the program.
    raw_status = row['status'] if 'status' in row.keys() else None
    # Check this condition so the code only runs when the required case is true.
    if raw_status in {'new', 'sent_to_responder', 'responded', 'closed', 'false_alarm'}:
        # Return this value so the function sends a result back to its caller.
        return raw_status

    # Check this condition so the code only runs when the required case is true.
    if row['closed']:
        # Return this value so the function sends a result back to its caller.
        return 'closed'
    # Check this condition so the code only runs when the required case is true.
    if row['responded']:
        # Return this value so the function sends a result back to its caller.
        return 'responded'
    # Check this condition so the code only runs when the required case is true.
    if row['notified']:
        # Return this value so the function sends a result back to its caller.
        return 'sent_to_responder'
    # Return this value so the function sends a result back to its caller.
    return 'new'


# Define the function build_incident_status so this reusable block of code can be called later.
def build_incident_status(status):
    # Check this condition so the code only runs when the required case is true.
    if status == 'closed':
        # Return this value so the function sends a result back to its caller.
        return 'Closed'
    # Check this condition so the code only runs when the required case is true.
    if status == 'responded':
        # Return this value so the function sends a result back to its caller.
        return 'Responded'
    # Check this condition so the code only runs when the required case is true.
    if status == 'sent_to_responder':
        # Return this value so the function sends a result back to its caller.
        return 'Active'
    # Check this condition so the code only runs when the required case is true.
    if status == 'false_alarm':
        # Return this value so the function sends a result back to its caller.
        return 'False Alarm'
    # Return this value so the function sends a result back to its caller.
    return 'New'


# Define the function serialize_accident so this reusable block of code can be called later.
def serialize_accident(row):
    # Store a value in status so it can be used later in the program.
    status = get_alert_status(row)
    # Store a value in notified so it can be used later in the program.
    notified = bool(row['notified']) or status in {'sent_to_responder', 'responded', 'closed'}
    # Store a value in responded so it can be used later in the program.
    responded = bool(row['responded']) or status in {'responded', 'closed'}
    # This line performs the next step of the current logic.
    closed = bool(row['closed']) or status == 'closed'
    # Store a value in sent_at so it can be used later in the program.
    sent_at = row['sent_at'] if 'sent_at' in row.keys() else None
    # Store a value in reported_at so it can be used later in the program.
    reported_at = row['reported_at'] if 'reported_at' in row.keys() else None
    # Store a value in responded_at so it can be used later in the program.
    responded_at = row['responded_at'] if 'responded_at' in row.keys() else None
    # Store a value in closed_at so it can be used later in the program.
    closed_at = row['closed_at'] if 'closed_at' in row.keys() else None
    # Store a value in effective_sent_at so it can be used later in the program.
    effective_sent_at = sent_at or reported_at
    # Return this value so the function sends a result back to its caller.
    return {
        # This line performs the next step of the current logic.
        'id': row['id'],
        # This line performs the next step of the current logic.
        'image': row['image'],
        # This line performs the next step of the current logic.
        'elapsed': get_elapsed_time(row['timestamp']),
        # This line performs the next step of the current logic.
        'date': datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
        # This line performs the next step of the current logic.
        'created_at': row['timestamp'],
        # This line performs the next step of the current logic.
        'sent_at': effective_sent_at,
        # This line performs the next step of the current logic.
        'responded_at': responded_at,
        # This line performs the next step of the current logic.
        'closed_at': closed_at,
        # Store a value in 'time_to_respond': format_duration(responded_at - effective_sent_at) if effective_sent_at and responded_at and responded_at > so it can be used later in the program.
        'time_to_respond': format_duration(responded_at - effective_sent_at) if effective_sent_at and responded_at and responded_at >= effective_sent_at else None,
        # Store a value in 'time_to_close': format_duration(closed_at - responded_at) if responded_at and closed_at and closed_at > so it can be used later in the program.
        'time_to_close': format_duration(closed_at - responded_at) if responded_at and closed_at and closed_at >= responded_at else None,
        # This line performs the next step of the current logic.
        'notified': notified,
        # This line performs the next step of the current logic.
        'responded': responded,
        # This line performs the next step of the current logic.
        'closed': closed,
        # This line performs the next step of the current logic.
        'internal_status': status,
        # This line performs the next step of the current logic.
        'status': build_incident_status(status)
    # This line performs the next step of the current logic.
    }


# Define the function build_dashboard_context so this reusable block of code can be called later.
def build_dashboard_context():
    # Store a value in role so it can be used later in the program.
    role = get_current_role()
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store a value in alert_counts so it can be used later in the program.
    alert_counts = get_alert_counts(conn)
    # Store a value in response_metrics so it can be used later in the program.
    response_metrics = get_average_response_time(conn)
    # Store a value in recent_events so it can be used later in the program.
    recent_events = build_recent_events(conn)
    # Store a value in events_today_count so it can be used later in the program.
    events_today_count = conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        SELECT COUNT(*) AS total
        # This line performs the next step of the current logic.
        FROM accidents
        # Store a value in WHERE date(timestamp, 'unixepoch', 'localtime') so it can be used later in the program.
        WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
        # This line performs the next step of the current logic.
        '''
    # This line performs the next step of the current logic.
    ).fetchone()['total'] or 0
    # Close the database connection to free resources.
    conn.close()

    # Store a value in user_email so it can be used later in the program.
    user_email = session.get('user_email', '')
    # Store a value in user_name so it can be used later in the program.
    user_name = user_email.split('@', 1)[0].replace('.', ' ').title() if user_email else role.title()
    # Return this value so the function sends a result back to its caller.
    return {
        # This line performs the next step of the current logic.
        **alert_counts,
        # This line performs the next step of the current logic.
        **response_metrics,
        # This line performs the next step of the current logic.
        'cameras_online_count': 124,
        # This line performs the next step of the current logic.
        'cameras_total_count': 128,
        # This line performs the next step of the current logic.
        'events_today_count': events_today_count,
        # This line performs the next step of the current logic.
        'recent_events': recent_events,
        # This line performs the next step of the current logic.
        'current_role': role,
        # This line performs the next step of the current logic.
        'dashboard_title': 'Dashboard',
        # This line performs the next step of the current logic.
        'user_name': user_name,
        # This line performs the next step of the current logic.
        'user_role_label': role.title(),
        # This line performs the next step of the current logic.
        'alerts_url': url_for('alerts_page'),
        # This line performs the next step of the current logic.
        'alerts_label': 'Active Alerts',
    # This line performs the next step of the current logic.
    }


# Define the function render_dashboard_page so this reusable block of code can be called later.
def render_dashboard_page():
    # Return this value so the function sends a result back to its caller.
    return render_template('home.html', **build_dashboard_context())


# Define the function get_post_login_endpoint so this reusable block of code can be called later.
def get_post_login_endpoint():
    # Return this value so the function sends a result back to its caller.
    return 'alerts_page' if get_current_role() == 'responder' else 'dashboard'


# Define the function login_required so this reusable block of code can be called later.
def login_required(f):
    # Import specific tools from another module so they can be used directly here.
    from functools import wraps
    # Apply a decorator to change or protect the behavior of the function below.
    @wraps(f)
    # Define the function decorated so this reusable block of code can be called later.
    def decorated(*args, **kwargs):
        # Check this condition so the code only runs when the required case is true.
        if 'user_id' not in session:
            # Return this value so the function sends a result back to its caller.
            return redirect(url_for('login'))
        # Return this value so the function sends a result back to its caller.
        return f(*args, **kwargs)
    # Return this value so the function sends a result back to its caller.
    return decorated


# Apply a decorator to change or protect the behavior of the function below.
@app.after_request
# Define the function add_no_cache_headers so this reusable block of code can be called later.
def add_no_cache_headers(response):
    # Check this condition so the code only runs when the required case is true.
    if 'user_id' in session and request.endpoint != 'static':
        # Store a value in response.headers['Cache-Control'] so it can be used later in the program.
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        # Store a value in response.headers['Pragma'] so it can be used later in the program.
        response.headers['Pragma'] = 'no-cache'
        # Store a value in response.headers['Expires'] so it can be used later in the program.
        response.headers['Expires'] = '0'
    # Return this value so the function sends a result back to its caller.
    return response


# Define the function responder_required so this reusable block of code can be called later.
def responder_required(f):
    # Import specific tools from another module so they can be used directly here.
    from functools import wraps
    # Apply a decorator to change or protect the behavior of the function below.
    @wraps(f)
    # Apply a decorator to change or protect the behavior of the function below.
    @login_required
    # Define the function decorated so this reusable block of code can be called later.
    def decorated(*args, **kwargs):
        # Check this condition so the code only runs when the required case is true.
        if get_current_role() != 'responder':
            # Return this value so the function sends a result back to its caller.
            return "Access Denied", 403
        # Return this value so the function sends a result back to its caller.
        return f(*args, **kwargs)
    # Return this value so the function sends a result back to its caller.
    return decorated


# Define the function admin_required so this reusable block of code can be called later.
def admin_required(f):
    # Import specific tools from another module so they can be used directly here.
    from functools import wraps
    # Apply a decorator to change or protect the behavior of the function below.
    @wraps(f)
    # Apply a decorator to change or protect the behavior of the function below.
    @login_required
    # Define the function decorated so this reusable block of code can be called later.
    def decorated(*args, **kwargs):
        # Check this condition so the code only runs when the required case is true.
        if get_current_role() != 'admin':
            # Return this value so the function sends a result back to its caller.
            return redirect(url_for('dashboard'))
        # Return this value so the function sends a result back to its caller.
        return f(*args, **kwargs)
    # Return this value so the function sends a result back to its caller.
    return decorated


# This existing comment notes: ─── Helpers ──────────────────────────────────────────────────
# ─── Helpers ──────────────────────────────────────────────────

# Define the function allowed_file so this reusable block of code can be called later.
def allowed_file(filename):
    # Return this value so the function sends a result back to its caller.
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Define the function get_elapsed_time so this reusable block of code can be called later.
def get_elapsed_time(timestamp):
    # Store a value in diff so it can be used later in the program.
    diff = time.time() - timestamp
    # Check this condition so the code only runs when the required case is true.
    if diff < 60:
        # Return this value so the function sends a result back to its caller.
        return f"{int(diff)} seconds ago"
    # Check another condition if the earlier condition was not true.
    elif diff < 3600:
        # Store a value in mins so it can be used later in the program.
        mins = int(diff // 60)
        # Return this value so the function sends a result back to its caller.
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    # Check another condition if the earlier condition was not true.
    elif diff < 86400:
        # Store a value in hours so it can be used later in the program.
        hours = int(diff // 3600)
        # Return this value so the function sends a result back to its caller.
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    # Run this block when the earlier condition did not match.
    else:
        # Store a value in days so it can be used later in the program.
        days = int(diff // 86400)
        # Return this value so the function sends a result back to its caller.
        return f"{days} day{'s' if days != 1 else ''} ago"


# Define the function format_duration so this reusable block of code can be called later.
def format_duration(seconds):
    # Store a value in total_seconds so it can be used later in the program.
    total_seconds = max(int(round(seconds)), 0)
    # Store a value in minutes, remaining_seconds so it can be used later in the program.
    minutes, remaining_seconds = divmod(total_seconds, 60)
    # Store a value in hours, remaining_minutes so it can be used later in the program.
    hours, remaining_minutes = divmod(minutes, 60)

    # Check this condition so the code only runs when the required case is true.
    if hours > 0:
        # Return this value so the function sends a result back to its caller.
        return f"{hours} hr {remaining_minutes} min"
    # Check this condition so the code only runs when the required case is true.
    if minutes > 0:
        # Return this value so the function sends a result back to its caller.
        return f"{minutes} min {remaining_seconds} sec"
    # Return this value so the function sends a result back to its caller.
    return f"{remaining_seconds} sec"


# Define the function fetch_accident_row so this reusable block of code can be called later.
def fetch_accident_row(conn, accident_id):
    # Return this value so the function sends a result back to its caller.
    return conn.execute('SELECT * FROM accidents WHERE id = ?', (accident_id,)).fetchone()


# Define the function accident_visible_to_admin so this reusable block of code can be called later.
def accident_visible_to_admin(row):
    # Return this value so the function sends a result back to its caller.
    return get_alert_status(row) not in {'closed', 'false_alarm'}


# Define the function accident_visible_to_responder so this reusable block of code can be called later.
def accident_visible_to_responder(row):
    # Return this value so the function sends a result back to its caller.
    return get_alert_status(row) in {'sent_to_responder', 'responded'}


# Define the function build_alert_action_response so this reusable block of code can be called later.
def build_alert_action_response(conn, accident_id, message):
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Store a value in accident so it can be used later in the program.
    accident = serialize_accident(row)
    # Store a value in alert_counts so it can be used later in the program.
    alert_counts = get_alert_counts(conn)
    # Return this value so the function sends a result back to its caller.
    return {
        # This line performs the next step of the current logic.
        'success': True,
        # This line performs the next step of the current logic.
        'id': accident_id,
        # This line performs the next step of the current logic.
        'status': accident['status'],
        # This line performs the next step of the current logic.
        'internal_status': accident['internal_status'],
        # This line performs the next step of the current logic.
        'notified': accident['notified'],
        # This line performs the next step of the current logic.
        'responded': accident['responded'],
        # This line performs the next step of the current logic.
        'closed': accident['closed'],
        # This line performs the next step of the current logic.
        'sent_at': accident['sent_at'],
        # This line performs the next step of the current logic.
        'responded_at': accident['responded_at'],
        # This line performs the next step of the current logic.
        'closed_at': accident['closed_at'],
        # This line performs the next step of the current logic.
        'time_to_respond': accident['time_to_respond'],
        # This line performs the next step of the current logic.
        'time_to_close': accident['time_to_close'],
        # This line performs the next step of the current logic.
        **alert_counts,
        # This line performs the next step of the current logic.
        'message': message,
    # This line performs the next step of the current logic.
    }


# Define the function report_alert_by_id so this reusable block of code can be called later.
def report_alert_by_id(accident_id):
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Check this condition so the code only runs when the required case is true.
    if not row:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    # Store a value in status so it can be used later in the program.
    status = get_alert_status(row)
    # Check this condition so the code only runs when the required case is true.
    if status == 'closed':
        # Store a value in response so it can be used later in the program.
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify(response)

    # Check this condition so the code only runs when the required case is true.
    if status in {'sent_to_responder', 'responded'}:
        # Store a value in response so it can be used later in the program.
        response = build_alert_action_response(conn, accident_id, 'Alert already sent to responder.')
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify(response)

    # Store a value in sent_at so it can be used later in the program.
    sent_at = row['sent_at'] or row['reported_at'] or time.time()
    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET notified so it can be used later in the program.
        SET notified = 1,
            # Store a value in status so it can be used later in the program.
            status = 'sent_to_responder',
            # Store a value in sent_at so it can be used later in the program.
            sent_at = ?,
            # Store a value in reported_at so it can be used later in the program.
            reported_at = COALESCE(reported_at, ?)
        # Store a value in WHERE id so it can be used later in the program.
        WHERE id = ?
        # This line performs the next step of the current logic.
        ''',
        # Continue building the value from the previous line.
        (sent_at, sent_at, accident_id)
    # This line performs the next step of the current logic.
    )
    # Save the database changes permanently.
    conn.commit()
    # Store a value in response so it can be used later in the program.
    response = build_alert_action_response(conn, accident_id, 'Alert sent to responder dashboard.')
    # Close the database connection to free resources.
    conn.close()
    # Return this value so the function sends a result back to its caller.
    return jsonify(response)


# Define the function false_alarm_by_id so this reusable block of code can be called later.
def false_alarm_by_id(accident_id):
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Check this condition so the code only runs when the required case is true.
    if not row:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    # Store a value in status so it can be used later in the program.
    status = get_alert_status(row)
    # Check this condition so the code only runs when the required case is true.
    if status == 'closed':
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Closed alerts cannot be marked as false alarms.'}), 400

    # Check this condition so the code only runs when the required case is true.
    if status in {'responded', 'sent_to_responder'}:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'This alert has already been sent to the responder dashboard.'}), 400

    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET status so it can be used later in the program.
        SET status = 'false_alarm',
            # Store a value in notified so it can be used later in the program.
            notified = 0,
            # Store a value in responded so it can be used later in the program.
            responded = 0,
            # Store a value in closed so it can be used later in the program.
            closed = 0
        # Store a value in WHERE id so it can be used later in the program.
        WHERE id = ?
        # This line performs the next step of the current logic.
        ''',
        # Continue building the value from the previous line.
        (accident_id,)
    # This line performs the next step of the current logic.
    )
    # Save the database changes permanently.
    conn.commit()
    # Store a value in response so it can be used later in the program.
    response = build_alert_action_response(conn, accident_id, 'Alert marked as false alarm and kept in the database.')
    # Close the database connection to free resources.
    conn.close()
    # Return this value so the function sends a result back to its caller.
    return jsonify(response)


# Define the function respond_alert_by_id so this reusable block of code can be called later.
def respond_alert_by_id(accident_id):
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Check this condition so the code only runs when the required case is true.
    if not row:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    # Store a value in status so it can be used later in the program.
    status = get_alert_status(row)
    # Check this condition so the code only runs when the required case is true.
    if status not in {'sent_to_responder', 'responded'} and not row['notified']:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'This alert has not been sent to responders yet'}), 400

    # Check this condition so the code only runs when the required case is true.
    if status == 'closed':
        # Store a value in response so it can be used later in the program.
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify(response)

    # Check this condition so the code only runs when the required case is true.
    if status == 'responded':
        # Store a value in response so it can be used later in the program.
        response = build_alert_action_response(conn, accident_id, 'Incident already marked as responded.')
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify(response)

    # Store a value in responded_at so it can be used later in the program.
    responded_at = time.time()
    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET responded so it can be used later in the program.
        SET responded = 1,
            # Store a value in notified so it can be used later in the program.
            notified = 1,
            # Store a value in status so it can be used later in the program.
            status = 'responded',
            # Store a value in responded_at so it can be used later in the program.
            responded_at = ?
        # Store a value in WHERE id so it can be used later in the program.
        WHERE id = ?
        # This line performs the next step of the current logic.
        ''',
        # Continue building the value from the previous line.
        (responded_at, accident_id)
    # This line performs the next step of the current logic.
    )
    # Save the database changes permanently.
    conn.commit()
    # Store a value in response so it can be used later in the program.
    response = build_alert_action_response(conn, accident_id, 'Incident marked as responded. You can close it now.')
    # Close the database connection to free resources.
    conn.close()
    # Return this value so the function sends a result back to its caller.
    return jsonify(response)


# Define the function close_alert_by_id so this reusable block of code can be called later.
def close_alert_by_id(accident_id):
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Check this condition so the code only runs when the required case is true.
    if not row:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    # Store a value in status so it can be used later in the program.
    status = get_alert_status(row)
    # Check this condition so the code only runs when the required case is true.
    if status not in {'responded', 'closed'} and not row['responded']:
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Alert must be responded to before it can be closed.'}), 400

    # Check this condition so the code only runs when the required case is true.
    if status == 'closed':
        # Store a value in response so it can be used later in the program.
        response = build_alert_action_response(conn, accident_id, 'Alert already closed.')
        # Close the database connection to free resources.
        conn.close()
        # Return this value so the function sends a result back to its caller.
        return jsonify(response)

    # Store a value in closed_at so it can be used later in the program.
    closed_at = time.time()
    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        '''
        # This line performs the next step of the current logic.
        UPDATE accidents
        # Store a value in SET responded so it can be used later in the program.
        SET responded = 1,
            # Store a value in closed so it can be used later in the program.
            closed = 1,
            # Store a value in status so it can be used later in the program.
            status = 'closed',
            # Store a value in closed_at so it can be used later in the program.
            closed_at = ?
        # Store a value in WHERE id so it can be used later in the program.
        WHERE id = ?
        # This line performs the next step of the current logic.
        ''',
        # Continue building the value from the previous line.
        (closed_at, accident_id)
    # This line performs the next step of the current logic.
    )
    # Save the database changes permanently.
    conn.commit()
    # Store a value in response so it can be used later in the program.
    response = build_alert_action_response(conn, accident_id, 'Incident closed and removed from active cases.')
    # Close the database connection to free resources.
    conn.close()
    # Return this value so the function sends a result back to its caller.
    return jsonify(response)


# Define the function save_snapshot_background so this reusable block of code can be called later.
def save_snapshot_background(frame_copy):
    # This line performs the next step of the current logic.
    """Save accident snapshot in a background thread — never blocks video."""
    # Store a value in accident_id so it can be used later in the program.
    accident_id = str(uuid.uuid4())[:8]
    # Store a value in filename so it can be used later in the program.
    filename = f"accident_{accident_id}.jpg"
    # Store a value in filepath so it can be used later in the program.
    filepath = os.path.join(ACCIDENTS_DIR, filename)

    # This line performs the next step of the current logic.
    cv2.imwrite(filepath, frame_copy, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Run this SQL command on the SQLite database connection.
    conn.execute(
        # This line performs the next step of the current logic.
        'INSERT INTO accidents (id, image, timestamp, notified, status) VALUES (?, ?, ?, 0, ?)',
        # Continue building the value from the previous line.
        (accident_id, filename, time.time(), 'new')
    # This line performs the next step of the current logic.
    )
    # Save the database changes permanently.
    conn.commit()
    # Close the database connection to free resources.
    conn.close()


# Define the function process_frame so this reusable block of code can be called later.
def process_frame(frame):
    # This line performs the next step of the current logic.
    """Run YOLO detection on a single frame and draw results.
    # This line performs the next step of the current logic.
    OPTIMIZED: no pandas, direct numpy array access, smaller inference size."""
    # Tell Python that this function will use and update a global variable.
    global accident_flag

    # Store a value in frame so it can be used later in the program.
    frame = cv2.resize(frame, (1020, 500))

    # This existing comment notes: Same as original code — no conf filter so all detections come through
    # Same as original code — no conf filter so all detections come through
    # Store a value in results so it can be used later in the program.
    results = model.predict(frame, verbose=False)

    # Store a value in boxes so it can be used later in the program.
    boxes = results[0].boxes
    # Store a value in accident_detected so it can be used later in the program.
    accident_detected = False

    # Check this condition so the code only runs when the required case is true.
    if boxes is not None and len(boxes) > 0:
        # This existing comment notes: Direct numpy access — no pandas overhead
        # Direct numpy access — no pandas overhead
        # Store incoming or processed data in this variable for later use.
        data = boxes.data.cpu().numpy()

        # Loop through these items one by one so the same steps can be repeated for each item.
        for row in data:
            # Store a value in x1, y1, x2, y2 so it can be used later in the program.
            x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
            # Store a value in conf so it can be used later in the program.
            conf = row[4]
            # Store a value in cls_id so it can be used later in the program.
            cls_id = int(row[5])

            # Store a value in label so it can be used later in the program.
            label = class_list[cls_id] if cls_id < len(class_list) else "Unknown"

            # Check this condition so the code only runs when the required case is true.
            if label == "accident":
                # Store a value in accident_detected so it can be used later in the program.
                accident_detected = True
                # This line performs the next step of the current logic.
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 4)
                # This line performs the next step of the current logic.
                cv2.putText(frame, f"ACCIDENT {conf:.0%}", (x1, y1 - 12),
                            # This line performs the next step of the current logic.
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
            # Run this block when the earlier condition did not match.
            else:
                # This line performs the next step of the current logic.
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                # This line performs the next step of the current logic.
                cv2.putText(frame, f"{label} {conf:.0%}", (x1, y1 - 10),
                            # This line performs the next step of the current logic.
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)

    # Check this condition so the code only runs when the required case is true.
    if accident_detected:
        # Store a value in h, w so it can be used later in the program.
        h, w = frame.shape[:2]
        # Store a value in overlay so it can be used later in the program.
        overlay = frame.copy()
        # This line performs the next step of the current logic.
        cv2.rectangle(overlay, (0, 0), (w, 65), (0, 0, 180), -1)
        # This line performs the next step of the current logic.
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Store a value in text so it can be used later in the program.
        text = "!!! ACCIDENT DETECTED !!!"
        # Store a value in font so it can be used later in the program.
        font = cv2.FONT_HERSHEY_DUPLEX
        # Store a value in (tw, th), _ so it can be used later in the program.
        (tw, th), _ = cv2.getTextSize(text, font, 1.1, 2)
        # Store a value in cx so it can be used later in the program.
        cx = (w - tw) // 2
        # This line performs the next step of the current logic.
        cv2.putText(frame, text, (cx + 2, 44), font, 1.1, (0, 0, 0), 4, cv2.LINE_AA)
        # This line performs the next step of the current logic.
        cv2.putText(frame, text, (cx, 42), font, 1.1, (0, 255, 255), 2, cv2.LINE_AA)

        # Check this condition so the code only runs when the required case is true.
        if int(time.time() * 3) % 2 == 0:
            # This line performs the next step of the current logic.
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 5)

        
        # Open and manage this resource safely, then clean it up automatically when the block ends.
        with accident_flag_lock:
            # Store a value in accident_flag so it can be used later in the program.
            accident_flag = True

    # Return this value so the function sends a result back to its caller.
    return frame, accident_detected


# Define the function try_save_snapshot so this reusable block of code can be called later.
def try_save_snapshot(frame):
    # This line performs the next step of the current logic.
    """Try to save a snapshot — only once per 30s, in background thread."""
    # Tell Python that this function will use and update a global variable.
    global last_snapshot_time
    # Store a value in now so it can be used later in the program.
    now = time.time()
    # Check this condition so the code only runs when the required case is true.
    if now - last_snapshot_time >= 30:
        # Store a value in last_snapshot_time so it can be used later in the program.
        last_snapshot_time = now
        # Store a value in snapshot so it can be used later in the program.
        snapshot = frame.copy()
        # Store a value in threading.Thread(target so it can be used later in the program.
        threading.Thread(target=save_snapshot_background, args=(snapshot,), daemon=True).start()


# Define the function generate_video_frames so this reusable block of code can be called later.
def generate_video_frames():
    # This line performs the next step of the current logic.
    """Generator for uploaded video streaming — OPTIMIZED."""
    # Tell Python that this function will use and update a global variable.
    global current_video_path, video_active

    # Check this condition so the code only runs when the required case is true.
    if not current_video_path or not os.path.exists(current_video_path):
        # Return from the function here without sending a specific value.
        return

    # Store a value in cap so it can be used later in the program.
    cap = cv2.VideoCapture(current_video_path)
    # Store a value in video_active so it can be used later in the program.
    video_active = True
    # Store a value in frame_count so it can be used later in the program.
    frame_count = 0

    # Keep looping while this condition stays true.
    while video_active:
        # Store a value in ret, frame so it can be used later in the program.
        ret, frame = cap.read()
        # Check this condition so the code only runs when the required case is true.
        if not ret:
            # This line performs the next step of the current logic.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            # Store a value in ret, frame so it can be used later in the program.
            ret, frame = cap.read()
            # Check this condition so the code only runs when the required case is true.
            if not ret:
                # Stop the loop here immediately.
                break

        # Store a value in frame_count + so it can be used later in the program.
        frame_count += 1
        # Check this condition so the code only runs when the required case is true.
        if frame_count % 2 != 0:
            # Skip the rest of this loop step and move to the next item.
            continue

        # Store a value in processed, detected so it can be used later in the program.
        processed, detected = process_frame(frame)

       
        # Check this condition so the code only runs when the required case is true.
        if detected:
            # This line performs the next step of the current logic.
            try_save_snapshot(processed)

        # Store a value in _, buffer so it can be used later in the program.
        _, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        # This line performs the next step of the current logic.
        yield (b'--frame\r\n'
               # This line performs the next step of the current logic.
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    # This line performs the next step of the current logic.
    cap.release()
    # Store a value in video_active so it can be used later in the program.
    video_active = False


# Define the function generate_camera_frames so this reusable block of code can be called later.
def generate_camera_frames():
    # This line performs the next step of the current logic.
    """Generator for live camera streaming — OPTIMIZED."""
    # Tell Python that this function will use and update a global variable.
    global camera_active

    # Store a value in cap so it can be used later in the program.
    cap = cv2.VideoCapture(0)
    # Check this condition so the code only runs when the required case is true.
    if not cap.isOpened():
        # Return from the function here without sending a specific value.
        return

    # This line performs the next step of the current logic.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    # This line performs the next step of the current logic.
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Store a value in camera_active so it can be used later in the program.
    camera_active = True
    # Store a value in frame_count so it can be used later in the program.
    frame_count = 0

    # Keep looping while this condition stays true.
    while camera_active:
        # Store a value in ret, frame so it can be used later in the program.
        ret, frame = cap.read()
        # Check this condition so the code only runs when the required case is true.
        if not ret:
            # Stop the loop here immediately.
            break

        # Store a value in frame_count + so it can be used later in the program.
        frame_count += 1
        # Check this condition so the code only runs when the required case is true.
        if frame_count % 2 != 0:
            # Skip the rest of this loop step and move to the next item.
            continue

        # Store a value in processed, detected so it can be used later in the program.
        processed, detected = process_frame(frame)

        
        # Check this condition so the code only runs when the required case is true.
        if detected:
            # This line performs the next step of the current logic.
            try_save_snapshot(processed)

        # Store a value in _, buffer so it can be used later in the program.
        _, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        # This line performs the next step of the current logic.
        yield (b'--frame\r\n'
               # This line performs the next step of the current logic.
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    # This line performs the next step of the current logic.
    cap.release()
    # Store a value in camera_active so it can be used later in the program.
    camera_active = False




# Register the web route below so Flask knows which URL should run the next function.
@app.route('/login', methods=['GET', 'POST'])
# Define the function login so this reusable block of code can be called later.
def login():
    # Check this condition so the code only runs when the required case is true.
    if 'user_id' in session:
        # Return this value so the function sends a result back to its caller.
        return redirect(url_for(get_post_login_endpoint()))

    # Store a value in error so it can be used later in the program.
    error = None
    # Store a value in success so it can be used later in the program.
    success = None
    # Store a value in show_signup so it can be used later in the program.
    show_signup = False

    # Check this condition so the code only runs when the required case is true.
    if request.method == 'POST':
        # Store a value in form_type so it can be used later in the program.
        form_type = request.form.get('form_type')
        # Store a value in email so it can be used later in the program.
        email = request.form.get('email', '').strip().lower()
        # Do nothing here on purpose.
        password = request.form.get('password', '')

        # Check this condition so the code only runs when the required case is true.
        if form_type == 'signup':
            # Store a value in show_signup so it can be used later in the program.
            show_signup = True
            # Store a value in confirm so it can be used later in the program.
            confirm = request.form.get('confirm_password', '')
            # Store a value in role so it can be used later in the program.
            role = request.form.get('role', 'admin').strip().lower()
            # Store a value in conn so it can be used later in the program.
            conn = get_db()

            # Check this condition so the code only runs when the required case is true.
            if not email or not password:
                # Store a value in error so it can be used later in the program.
                error = 'Please fill in all fields.'
            # Check another condition if the earlier condition was not true.
            elif '@' not in email or '.' not in email:
                # Store a value in error so it can be used later in the program.
                error = 'Please enter a valid email address.'
            # Check another condition if the earlier condition was not true.
            elif len(password) < 4:
                # Store a value in error so it can be used later in the program.
                error = 'Password must be at least 4 characters.'
            # Check another condition if the earlier condition was not true.
            elif password != confirm:
                # Store a value in error so it can be used later in the program.
                error = 'Passwords do not match.'
            # Check another condition if the earlier condition was not true.
            elif role not in {'admin', 'responder'}:
                # Store a value in error so it can be used later in the program.
                error = 'Please select a valid role.'
            # Run this block when the earlier condition did not match.
            else:
                # Store a value in existing so it can be used later in the program.
                existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
                # Check this condition so the code only runs when the required case is true.
                if existing:
                    # Store a value in error so it can be used later in the program.
                    error = 'An account with this email already exists.'
                # Run this block when the earlier condition did not match.
                else:
                    # Run this SQL command on the SQLite database connection.
                    conn.execute(
                        # This line performs the next step of the current logic.
                        'INSERT INTO users (email, password, role) VALUES (?, ?, ?)',
                        # Continue building the value from the previous line.
                        (email, hash_password(password), role)
                    # This line performs the next step of the current logic.
                    )
                    # Save the database changes permanently.
                    conn.commit()
                    # Store a value in success so it can be used later in the program.
                    success = 'Account created successfully! Please sign in.'
                    # Store a value in show_signup so it can be used later in the program.
                    show_signup = False
            # Close the database connection to free resources.
            conn.close()

        # Check another condition if the earlier condition was not true.
        elif form_type == 'login':
            # Store a value in conn so it can be used later in the program.
            conn = get_db()
            # Store a value in user so it can be used later in the program.
            user = conn.execute(
                # Store a value in 'SELECT * FROM users WHERE email so it can be used later in the program.
                'SELECT * FROM users WHERE email = ? AND password = ?',
                # Continue building the value from the previous line.
                (email, hash_password(password))
            # This line performs the next step of the current logic.
            ).fetchone()
            # Close the database connection to free resources.
            conn.close()

            # Check this condition so the code only runs when the required case is true.
            if user:
                # Save a value in the user session so it can be remembered between requests.
                session['user_id'] = user['id']
                # Save a value in the user session so it can be remembered between requests.
                session['user_email'] = email
                # Save a value in the user session so it can be remembered between requests.
                session['user_role'] = user['role'] if user['role'] in {'admin', 'responder'} else 'admin'
                # Return this value so the function sends a result back to its caller.
                return redirect(url_for(get_post_login_endpoint()))
            # Run this block when the earlier condition did not match.
            else:
                # Store a value in error so it can be used later in the program.
                error = 'Invalid email or password.'

    # Return this value so the function sends a result back to its caller.
    return render_template('login.html', error=error, success=success, show_signup=show_signup)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/logout')
# Define the function logout so this reusable block of code can be called later.
def logout():
    # This line performs the next step of the current logic.
    session.clear()
    # Store a value in response so it can be used later in the program.
    response = redirect(url_for('intro'))
    # Store a value in response.headers['Cache-Control'] so it can be used later in the program.
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    # Store a value in response.headers['Pragma'] so it can be used later in the program.
    response.headers['Pragma'] = 'no-cache'
    # Store a value in response.headers['Expires'] so it can be used later in the program.
    response.headers['Expires'] = '0'
    # Return this value so the function sends a result back to its caller.
    return response


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/')
# Define the function intro so this reusable block of code can be called later.
def intro():
    # Return this value so the function sends a result back to its caller.
    return render_template('intro.html')


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/home', endpoint='home')
# Apply a decorator to change or protect the behavior of the function below.
@login_required
# Define the function home so this reusable block of code can be called later.
def home():
    # Return this value so the function sends a result back to its caller.
    return redirect(url_for(get_post_login_endpoint()))


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/dashboard', endpoint='dashboard')
# Apply a decorator to change or protect the behavior of the function below.
@login_required
# Define the function dashboard so this reusable block of code can be called later.
def dashboard():
    # Return this value so the function sends a result back to its caller.
    return render_dashboard_page()


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/detect')
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function detect so this reusable block of code can be called later.
def detect():
    # Return this value so the function sends a result back to its caller.
    return render_template('detect.html', **build_dashboard_context())


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/alerts')
# Apply a decorator to change or protect the behavior of the function below.
@login_required
# Define the function alerts_page so this reusable block of code can be called later.
def alerts_page():
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store the rows returned by this query so they can be used later.
    rows = conn.execute('SELECT * FROM accidents ORDER BY timestamp DESC').fetchall()
    # Store a value in alert_counts so it can be used later in the program.
    alert_counts = get_alert_counts(conn)
    # Close the database connection to free resources.
    conn.close()

    # Check this condition so the code only runs when the required case is true.
    if get_current_role() == 'responder':
        # Build the accidents list that will be shown in the interface.
        accidents = [serialize_accident(row) for row in rows if accident_visible_to_responder(row)]
    # Run this block when the earlier condition did not match.
    else:
        # Build the accidents list that will be shown in the interface.
        accidents = [serialize_accident(row) for row in rows if accident_visible_to_admin(row)]

    # Build a context dictionary that will be passed into the template.
    context = build_dashboard_context()
    # This line performs the next step of the current logic.
    context.update(alert_counts)

    # Check this condition so the code only runs when the required case is true.
    if get_current_role() == 'responder':
        # Return this value so the function sends a result back to its caller.
        return render_template('respond.html', accidents=accidents, **context)
    # Return this value so the function sends a result back to its caller.
    return render_template('alerts.html', accidents=accidents, **context)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/report_alert/<accident_id>', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function report_alert so this reusable block of code can be called later.
def report_alert(accident_id):
    # Return this value so the function sends a result back to its caller.
    return report_alert_by_id(accident_id)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/false_alarm/<accident_id>', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function false_alarm so this reusable block of code can be called later.
def false_alarm(accident_id):
    # Return this value so the function sends a result back to its caller.
    return false_alarm_by_id(accident_id)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/respond_alert/<accident_id>', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@responder_required
# Define the function respond_alert so this reusable block of code can be called later.
def respond_alert(accident_id):
    # Return this value so the function sends a result back to its caller.
    return respond_alert_by_id(accident_id)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/close_alert/<accident_id>', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@responder_required
# Define the function close_alert so this reusable block of code can be called later.
def close_alert(accident_id):
    # Return this value so the function sends a result back to its caller.
    return close_alert_by_id(accident_id)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/contact_authority', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@login_required
# Define the function contact_authority so this reusable block of code can be called later.
def contact_authority():
    # Check this condition so the code only runs when the required case is true.
    if get_current_role() != 'admin':
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    # Store incoming or processed data in this variable for later use.
    data = request.get_json() or {}
    # Store a value in accident_id so it can be used later in the program.
    accident_id = data.get('id')
    # Return this value so the function sends a result back to its caller.
    return report_alert_by_id(accident_id)


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/mark_responded', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@login_required
# Define the function mark_responded so this reusable block of code can be called later.
def mark_responded():
    # Check this condition so the code only runs when the required case is true.
    if get_current_role() != 'responder':
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    # Store incoming or processed data in this variable for later use.
    data = request.get_json() or {}
    # Store a value in accident_id so it can be used later in the program.
    accident_id = data.get('id')
    # Store a value in conn so it can be used later in the program.
    conn = get_db()
    # Store one database row or computed value in the row variable for later use.
    row = fetch_accident_row(conn, accident_id)
    # Close the database connection to free resources.
    conn.close()
    # Check this condition so the code only runs when the required case is true.
    if not row:
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Accident not found'}), 404

    # Check this condition so the code only runs when the required case is true.
    if get_alert_status(row) == 'responded' or row['responded']:
        # Return this value so the function sends a result back to its caller.
        return close_alert_by_id(accident_id)
    # Return this value so the function sends a result back to its caller.
    return respond_alert_by_id(accident_id)




# Register the web route below so Flask knows which URL should run the next function.
@app.route('/upload', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function upload_video so this reusable block of code can be called later.
def upload_video():
    # Tell Python that this function will use and update a global variable.
    global current_video_path, video_active

    # Store a value in video_active so it can be used later in the program.
    video_active = False
    # This line performs the next step of the current logic.
    time.sleep(0.3)

    # Check this condition so the code only runs when the required case is true.
    if 'video' not in request.files:
        # Return this value so the function sends a result back to its caller.
        return jsonify({'error': 'No video file provided'}), 400

    # Store a value in file so it can be used later in the program.
    file = request.files['video']
    # Check this condition so the code only runs when the required case is true.
    if file.filename == '':
        # Return this value so the function sends a result back to its caller.
        return jsonify({'error': 'No file selected'}), 400

    # Check this condition so the code only runs when the required case is true.
    if not allowed_file(file.filename):
        # Return this value so the function sends a result back to its caller.
        return jsonify({'error': 'Invalid file type. Allowed: mp4, avi, mov, mkv, wmv, webm'}), 400

    # Store a value in filename so it can be used later in the program.
    filename = secure_filename(file.filename)
    # Store a value in filepath so it can be used later in the program.
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    # This line performs the next step of the current logic.
    file.save(filepath)
    # Store a value in current_video_path so it can be used later in the program.
    current_video_path = filepath

    # Return this value so the function sends a result back to its caller.
    return jsonify({'success': True, 'filename': filename})


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/video_feed')
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function video_feed so this reusable block of code can be called later.
def video_feed():
    # Return this value so the function sends a result back to its caller.
    return Response(generate_video_frames(),
                    # Store a value in mimetype so it can be used later in the program.
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/camera_feed')
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function camera_feed so this reusable block of code can be called later.
def camera_feed():
    # Return this value so the function sends a result back to its caller.
    return Response(generate_camera_frames(),
                    # Store a value in mimetype so it can be used later in the program.
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/process_camera_frame', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function process_camera_frame so this reusable block of code can be called later.
def process_camera_frame():
    # Start a block that may fail so errors can be handled safely.
    try:
        # Store a value in frame so it can be used later in the program.
        frame = None

        # Check this condition so the code only runs when the required case is true.
        if 'frame' in request.files:
            # Store a value in image_bytes so it can be used later in the program.
            image_bytes = request.files['frame'].read()
        # Run this block when the earlier condition did not match.
        else:
            # Store incoming or processed data in this variable for later use.
            data = request.get_json(silent=True) or {}
            # Store a value in frame_data so it can be used later in the program.
            frame_data = data.get('frame') or data.get('image')

            # Check this condition so the code only runs when the required case is true.
            if not frame_data:
                # Return this value so the function sends a result back to its caller.
                return jsonify({'success': False, 'error': 'No frame provided'}), 400

            # Check this condition so the code only runs when the required case is true.
            if ',' in frame_data:
                # Store a value in frame_data so it can be used later in the program.
                frame_data = frame_data.split(',', 1)[1]

            # Store a value in image_bytes so it can be used later in the program.
            image_bytes = base64.b64decode(frame_data)

        # Store a value in np_buffer so it can be used later in the program.
        np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
        # Store a value in frame so it can be used later in the program.
        frame = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    # Handle an error here if the code in the try block fails.
    except Exception:
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Invalid frame data'}), 400

    # Check this condition so the code only runs when the required case is true.
    if frame is None:
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': 'Unable to decode frame'}), 400

    # Start a block that may fail so errors can be handled safely.
    try:
        # Store a value in processed, detected so it can be used later in the program.
        processed, detected = process_frame(frame)

        # Check this condition so the code only runs when the required case is true.
        if detected:
            # This line performs the next step of the current logic.
            try_save_snapshot(processed)

        # Store a value in ok, buffer so it can be used later in the program.
        ok, buffer = cv2.imencode('.jpg', processed, ENCODE_PARAM)
        # Check this condition so the code only runs when the required case is true.
        if not ok:
            # Return this value so the function sends a result back to its caller.
            return jsonify({'success': False, 'error': 'Unable to encode frame'}), 500

        # Store a value in encoded so it can be used later in the program.
        encoded = base64.b64encode(buffer.tobytes()).decode('ascii')
        # Return this value so the function sends a result back to its caller.
        return jsonify({
            # This line performs the next step of the current logic.
            'success': True,
            # This line performs the next step of the current logic.
            'detected': detected,
            # This line performs the next step of the current logic.
            'image': f'data:image/jpeg;base64,{encoded}'
        # This line performs the next step of the current logic.
        })
    # Handle an error here if the code in the try block fails.
    except Exception as exc:
        # Return this value so the function sends a result back to its caller.
        return jsonify({'success': False, 'error': f'Camera processing failed: {exc}'}), 500


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/stop_video', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function stop_video so this reusable block of code can be called later.
def stop_video():
    # Tell Python that this function will use and update a global variable.
    global video_active
    # Store a value in video_active so it can be used later in the program.
    video_active = False
    # Return this value so the function sends a result back to its caller.
    return jsonify({'success': True})


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/stop_camera', methods=['POST'])
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function stop_camera so this reusable block of code can be called later.
def stop_camera():
    # Tell Python that this function will use and update a global variable.
    global camera_active
    # Store a value in camera_active so it can be used later in the program.
    camera_active = False
    # Return this value so the function sends a result back to its caller.
    return jsonify({'success': True})


# Register the web route below so Flask knows which URL should run the next function.
@app.route('/accident_status')
# Apply a decorator to change or protect the behavior of the function below.
@admin_required
# Define the function accident_status so this reusable block of code can be called later.
def accident_status():
    # This line performs the next step of the current logic.
    """Polled by browser to trigger sound — returns True once then resets."""
    # Tell Python that this function will use and update a global variable.
    global accident_flag
    # Open and manage this resource safely, then clean it up automatically when the block ends.
    with accident_flag_lock:
        # Store a value in status so it can be used later in the program.
        status = accident_flag
        # Store a value in accident_flag so it can be used later in the program.
        accident_flag = False
    # Return this value so the function sends a result back to its caller.
    return jsonify({'accident': status})



# Check this condition so the code only runs when the required case is true.
if __name__ == '__main__':
    # Store a value in app.run(debug so it can be used later in the program.
    app.run(debug=True, host='0.0.0.0', port=5000,
            # Store a value in use_reloader so it can be used later in the program.
            use_reloader=False, threaded=True)
