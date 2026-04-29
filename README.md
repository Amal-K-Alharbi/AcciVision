# AcciVision — Traffic Accident Detection System

## Project Overview

### Purpose
**AcciVision** is a real-time accident detection and alert management system that uses computer vision to automatically identify accidents in video streams (from cameras or uploaded files) and orchestrate a response workflow for operators and responders.

### Problem Solved
- **Manual monitoring is inefficient**: Security personnel cannot watch multiple video feeds continuously.
- **Slow response to accidents**: Delayed detection leads to worse outcomes.
- **No automated evidence capture**: Important for insurance and legal processes.
- **Disconnected workflows**: Detection, alerting, and response are typically siloed.

AcciVision automates detection, captures evidence, and provides a unified dashboard for admins and responders to manage the incident lifecycle.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Flask (Python web framework) |
| **ML Model** | YOLOv8 (Ultralytics) — trained on custom accident dataset |
| **Database** | SQLite (lightweight, file-based) |
| **Computer Vision** | OpenCV (`cv2`) |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript |
| **Deployment** | Local/Windows (uses `CAP_DSHOW` for camera) |

---

## System Workflow

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌─────────────┐
│   Input     │───▶│  Processing  │───▶│   Decision      │───▶│   Output    │
│ (Camera/    │    │ (YOLO        │    │ (Confidence     │    │ (Alert/     │
│  Video)     │    │  Inference)  │    │  Threshold)     │    │  Dashboard) │
└─────────────┘    └──────────────┘    └─────────────────┘    └─────────────┘
```

### Step-by-Step Flow

1. **Input Source**
   - Live camera feed (webcam or IP camera via OpenCV)
   - Uploaded video file (MP4, AVI, MOV, MKV, WMV, WebM)

2. **Frame Extraction**
   - Videos are read frame-by-frame using OpenCV
   - Frame skipping (`FRAME_SKIP_INTERVAL = 2`) reduces processing load

3. **Preprocessing**
   - Frame resized to 1020×500 for consistent input
   - Converted to numpy array for YOLO inference

4. **Model Inference**
   - YOLOv8 model (best.pt) processes each frame
   - Input size: 640×640 (`MODEL_INPUT_SIZE`)
   - Output: bounding boxes, confidence scores, class IDs

5. **Post-Processing**
   - Filter detections by class (only "accident" triggers alert)
   - Draw bounding boxes on frame for visualization
   - Add overlay warning text when accident detected

6. **Alert Decision**
   - If `confidence >= 0.80` → **auto-send to responder**
   - Otherwise → save as "new" alert for admin review

7. **Evidence Capture**
   - Snapshot saved to accidents
   - Record inserted into SQLite database
   - Throttled to once per 30 seconds to avoid duplicates

8. **Dashboard & Response**
   - Admin views all active alerts → can report, mark false alarm, or close
   - Responder sees only dispatched alerts → can acknowledge and close

---

## Component Breakdown

### 1. Backend Application (app.py)

| Component | Purpose |
|-----------|---------|
| **Model Loading** | `YOLO(MODEL_PATH)` — loaded once at startup |
| **Database Schema** | `users` table (auth), `accidents` table (evidence) |
| **Authentication** | SHA-256 password hashing, session-based auth |
| **Role System** | `admin` (full access), `responder` (limited access) |
| **Streaming Endpoints** | `/video_feed`, `/camera_feed` — MJPEG streams |
| **API Endpoints** | `/report_alert`, `/respond_alert`, `/close_alert`, `/false_alarm` |

**Key Functions:**
- `process_frame(frame)` — runs YOLO inference, draws boxes, returns detection result
- `try_save_snapshot()` — throttled background snapshot saver
- `build_dashboard_context()` — aggregates metrics for UI

### 2. Frontend Templates

| Template | Purpose |
|----------|---------|
| `intro.html` | Public landing page |
| `login.html` | Sign in / Sign up with role selection |
| home.html | Admin dashboard with stats cards |
| detect.html | Live detection workspace (upload/video/camera) |
| alerts.html | Admin alert management list |
| `respond.html` | Responder-only alert queue |
| `sidebar.html` | Shared navigation component |

### 3. Styling (style.css)

- Dark theme with cyberpunk/command-center aesthetic
- CSS variables for consistent theming
- Responsive grid layout for dashboard cards

### 4. Database Schema

```sql
-- Users table
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE,
    password TEXT,
    role TEXT DEFAULT 'admin'
);

-- Accidents table
CREATE TABLE accidents (
    id TEXT PRIMARY KEY,
    image TEXT,
    timestamp REAL,
    notified INTEGER DEFAULT 0,
    responded INTEGER DEFAULT 0,
    closed INTEGER DEFAULT 0,
    status TEXT,
    sent_at REAL,
    reported_at REAL,
    responded_at REAL,
    closed_at REAL,
    detection_time_seconds REAL
);
```

---

## Machine Learning Model Details

### Model: YOLOv8 (Custom Trained)

| Property | Value |
|----------|-------|
| **Architecture** | YOLOv8n (nano) — lightweight |
| **Input Size** | 640×640 pixels |
| **Classes** | `accident`, `cars` (from coco.txt) |
| **Weights File** | best.pt |

### How It Works (Simplified)

1. **Input**: A video frame (RGB image, ~500×1020 pixels)
2. **Preprocessing**: Resized to 640×640, normalized
3. **Inference**: The neural network outputs:
   - Bounding boxes (x1, y1, x2, y2)
   - Confidence score (0.0–1.0)
   - Class ID (0=accident, 1=cars)
4. **Post-processing**:
   - Filter by confidence threshold
   - If class="accident" and conf ≥ 0.80 → auto-escalate
   - Otherwise → mark as "new" for manual review

### Prediction Output

```python
# Example output from process_frame()
{
    'frame': <annotated numpy array>,
    'accident_detected': True,
    'auto_send_to_responder': True,  # conf >= 0.80
    'detection_time_seconds': 0.045  # inference latency
}
```

---

## Key Features

### Strengths

| Feature | Benefit |
|---------|---------|
| **Real-time streaming** | MJPEG feeds via Flask Response — no WebSocket needed |
| **Auto-escalation** | High-confidence detections automatically sent to responders |
| **Role-based access** | Admins see all; responders see only dispatched cases |
| **Evidence persistence** | Snapshots stored as JPEG, records in SQLite |
| **Performance optimization** | Frame skipping, throttled snapshots, cached camera discovery |
| **Background processing** | Snapshot saving runs in daemon thread — never blocks video |

### Possible Improvements

| Area | Suggestion |
|------|------------|
| **Performance** | Use GPU inference (`device='cuda'`) for lower latency |
| **Scalability** | Replace SQLite with PostgreSQL for multi-user concurrent writes |
| **Reliability** | Add model hot-reload without restarting Flask |
| **Monitoring** | Add Prometheus metrics for detection latency, alert volume |
| **Security** | Add rate limiting on auth endpoints, CSRF protection |
| **Mobile** | Add PWA manifest for offline-capable mobile dashboard |
| **Alerting** | Integrate Twilio/SMTP for push notifications to responders |

---

## API Routes Summary

| Route | Method | Role | Description |
|-------|--------|------|--------------|
| `/` | GET | Public | Intro page |
| `/login` | GET/POST | Public | Auth entry |
| `/logout` | GET | Auth | Session end |
| `/dashboard` | GET | Admin | Stats overview |
| `/detect` | GET | Admin | Detection workspace |
| `/alerts` | GET | Auth | Alert list (role-filtered) |
| `/upload` | POST | Admin | Video upload handler |
| `/video_feed` | GET | Admin | MJPEG stream |
| `/camera_feed` | GET | Admin | Live camera stream |
| `/report_alert/<id>` | POST | Admin | Send to responder |
| `/respond_alert/<id>` | POST | Responder | Acknowledge |
| `/close_alert/<id>` | POST | Responder | Resolve |
| `/false_alarm/<id>` | POST | Admin | Dismiss |

---

## Summary

AcciVision is an accident detection system that:

1. **Detects** accidents in real time using YOLOv8
2. **Captures** evidence automatically with throttled snapshots
3. **Alerts** operators via a dashboard with role-based views
4. **Tracks** the full incident lifecycle (new → sent → responded → closed)
5. **Streams** processed video frames to the browser without complex infrastructure

