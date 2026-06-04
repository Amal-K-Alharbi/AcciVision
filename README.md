<div align="center">

# 🚦 AcciVision

### AI-Powered Traffic Accident Detection & Response Dashboard

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Flask" src="https://img.shields.io/badge/Flask-Web_App-000000?style=for-the-badge&logo=flask&logoColor=white">
  <img alt="YOLOv8" src="https://img.shields.io/badge/YOLOv8-Ultralytics-6A5ACD?style=for-the-badge">
  <img alt="OpenCV" src="https://img.shields.io/badge/OpenCV-Computer_Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-Local_Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white">
</p>

<p>
  <img alt="Status" src="https://img.shields.io/badge/status-local_AI_system-success?style=flat-square">
  <img alt="Roles" src="https://img.shields.io/badge/roles-admin_%7C_responder-blue?style=flat-square">
  <img alt="Uploads" src="https://img.shields.io/badge/video_uploads-200MB_max-orange?style=flat-square">
  <img alt="Database" src="https://img.shields.io/badge/storage-accivision.db-lightgrey?style=flat-square">
</p>

AcciVision is a Flask-based accident monitoring system that runs a custom YOLO model on uploaded traffic videos or browser camera frames, saves accident snapshots, and routes incidents through an admin review and responder workflow.

</div>

---

## ✨ What This Project Does

| Area | Current Implementation |
| --- | --- |
| 🎥 Detection input | Admin users can upload supported video files or open the device camera from the browser. |
| 🧠 AI model | `best.pt` is loaded once at startup with Ultralytics YOLO and labels from `coco.txt`. |
| 🖼️ Frame processing | Frames are resized, analyzed by YOLO, annotated with bounding boxes, and returned to the browser. |
| 🚨 Alert signal | Accident detections set a server-side flag; the browser polls it and plays `warning.mp3`. |
| 📸 Evidence | Detected accident frames are saved as JPEG snapshots in `static/accidents/`. |
| 🗂️ Persistence | SQLite stores users, saved accident records, lifecycle status, responder status, and timestamps. |
| 👤 Admin workflow | Admins run detection, review incidents, approve alerts, or mark false alarms. |
| 🚑 Responder workflow | Responders see dispatched alerts, inspect evidence, update status, and resolve cases. |

---

## 🧭 System Workflow

```mermaid
flowchart LR
    A[Public Intro] --> B[Role Selection]
    B --> C[Admin Signup/Login]
    B --> D[Responder Signup/Login]

    C --> E[Admin Dashboard]
    E --> F[Test Model]
    F --> G[Video Upload]
    F --> H[Browser Camera]
    G --> I[YOLO Frame Processing]
    H --> I
    I --> J{Accident label >= 68% confirmed?}
    J -- No --> K[Annotated Feed Continues]
    J -- Yes --> L[Warning Overlay + Audio Flag]
    L --> M[Snapshot Saved, throttled to 30s]
    M --> N[New Accident Record]
    N --> O[Admin Alerts]
    O --> P[Approve and Send]
    O --> Q[False Alarm]
    P --> R[Responder Queue]
    R --> S[Acknowledge / En Route / On Scene / Resolved]
```

---

## 🏗️ Architecture

```mermaid
flowchart TB
    subgraph Client[Browser UI]
        Intro[intro.html]
        Login[login.html]
        AdminDash[home.html]
        Detect[detect.html]
        AdminAlerts[alerts.html]
        ResponderDash[respond.html]
        ResponderAlerts[responder_alert.html]
    end

    subgraph Flask[app.py]
        Auth[Authentication + Role Guards]
        Routes[Routes + JSON Actions]
        Pipeline[process_frame Pipeline]
        Streams[Video/Camera Streams]
        Alerts[Alert State Transitions]
        DBLayer[SQLite Helpers]
    end

    subgraph AI[Detection Assets]
        Model[best.pt]
        Labels[coco.txt]
    end

    subgraph Storage[Local Storage]
        DB[(accivision.db)]
        Uploads[uploads/]
        Snapshots[static/accidents/]
        Sound[static/assets/warning.mp3]
    end

    Client <--> Flask
    Pipeline --> Model
    Pipeline --> Labels
    Routes <--> DBLayer
    DBLayer <--> DB
    Streams <--> Uploads
    Pipeline --> Snapshots
    Detect --> Sound
```

---

## 🧠 Accident Detection Pipeline

The core detection path lives in `process_frame(frame)` inside `app.py`.

| Step | Code Behavior |
| --- | --- |
| 1. Normalize frame | Every processed frame is resized to `768 x 432`. |
| 2. Run YOLO | `model.predict(frame, imgsz=512, conf=0.45, verbose=False)` runs inference. |
| 3. Read detections | The app reads each YOLO box, confidence score, and class ID. |
| 4. Resolve label | Class IDs are mapped through `coco.txt`; the `accident` label is the only label that can trigger an incident. |
| 5. Draw overlays | Accident boxes at or above `0.68` are red; all other detections are green. |
| 6. Confirm warning | A warning is confirmed after at least `2` accident frames and `0.6` seconds from first sighting. |
| 7. Trigger warning | Confirmed accidents get a warning banner and set `accident_flag = True` once per detection run. |
| 8. Save snapshot | Streaming routes call `try_save_snapshot(processed)` when the confirmed detection flag is true. |
| 9. Throttle evidence | Snapshots are saved at most once every `30` seconds. |
| 10. Persist alert | A background thread writes the JPEG and inserts an `accidents` row. |

```mermaid
sequenceDiagram
    participant Source as Video/Camera Frame
    participant Flask as Flask Route
    participant CV as process_frame()
    participant YOLO as best.pt
    participant UI as Browser UI
    participant DB as SQLite

    Source->>Flask: Frame received
    Flask->>CV: Resize + process
    CV->>YOLO: Predict imgsz=512, conf=0.45
    YOLO-->>CV: Boxes, classes, scores
    CV-->>Flask: Annotated frame + detected flag
    Flask-->>UI: MJPEG frame or base64 JPEG
    alt accident label confidence >= 0.68 and confirmed
        CV->>CV: Set accident_flag
        Flask->>DB: Save snapshot record, throttled
        UI->>Flask: Poll /accident_status
        Flask-->>UI: accident=true once
        UI->>UI: Play warning.mp3
    end
```

### Detection Confidence Logic

| Threshold | Meaning |
| --- | --- |
| `0.45` | YOLO prediction confidence threshold used by `model.predict(...)`. |
| `>= 0.68` | A detection contributes to an accident only when its label is `accident` and confidence is at least 68%. |
| 2 frames + 0.6 seconds | Accident detections must persist long enough to become a confirmed accident alert. |
| 1.5 seconds | If accident detections disappear for longer than this, the confirmation state and one-shot alert flag reset. |
| 30 seconds | Snapshot throttle interval between saved accident images. |

> Current code note: the database has `confidence`, `source_video`, and `detection_time_seconds` columns, but the active snapshot insert only stores `id`, `image`, `timestamp`, and `notified`. Therefore confidence and detection-time dashboard values may remain default/empty unless existing database rows already contain those fields.

---

## 🎥 Live Camera & Video Upload Workflows

### Uploaded Video

```mermaid
flowchart TD
    A[Admin opens Test Model page] --> B[Selects or drags video file]
    B --> C[Send video to upload endpoint]
    C --> D[Validate file type and size limit]
    D --> E[Save video inside uploads folder]
    E --> F[Browser opens video feed endpoint]
    F --> G[OpenCV reads video frames]
    G --> H[Run accident detection function]
    H --> I[Return annotated video stream]
    I --> J[Browser displays processed video]
    J --> K[Delete uploaded file after stop or completion]
```

Supported upload extensions:

`mp4`, `avi`, `mov`, `mkv`, `wmv`, `webm`

---

### Browser Camera

```mermaid
flowchart TD
    A[Admin clicks Open Camera] --> B[Browser requests camera permission]
    B --> C[Camera stream appears in browser]
    C --> D[Browser captures frame from video]
    D --> E[Convert frame to JPEG blob]
    E --> F[Send frame to process camera frame endpoint]
    F --> G[Decode frame using OpenCV]
    G --> H[Run accident detection function]
    H --> I[Return annotated base64 image]
    I --> J[Browser updates live preview]
```

There is also a server-side `/camera_feed` route that uses OpenCV camera index `0`, but the current `detect.html` browser workflow uses `/process_camera_frame`.

---

## 👥 Roles & Workflows

| Capability | Admin / Operator | Responder |
| --- | ---: | ---: |
| View dashboard | ✅ | ✅ |
| Run uploaded-video detection | ✅ | ❌ |
| Run browser-camera detection | ✅ | ❌ |
| Review all non-false-alarm incidents | ✅ | ❌ |
| Approve and send alerts | ✅ | ❌ |
| Mark new incidents as false alarms | ✅ | ❌ |
| View dispatched alerts | ❌ | ✅ |
| Update response status | ❌ | ✅ |
| Resolve incident | ❌ | ✅ |

### Admin Flow

1. Register or log in as `admin`.
2. Open **Dashboard** for live operational metrics.
3. Open **Test Model** to upload footage or start browser-camera detection.
4. Review saved detections on **Alerts**.
5. Choose **Approve and Send** to route an incident to responders.
6. Choose **False Alarm** to hide an unreported incident from live queues while keeping it in the database.

### Responder Flow

1. Register or log in as `responder`.
2. Land on the **Responder Dashboard**.
3. View assigned/dispatched alerts.
4. Open alert details to inspect the saved snapshot.
5. Update the response state: `Acknowledged`, `En Route`, `On Scene`, or `Resolved`.
6. Resolving an alert closes the incident.

---

## 📊 Dashboards & Navigation

| Page | Template | Access | Purpose |
| --- | --- | --- | --- |
| Intro | `intro.html` | Public | Public entry page. |
| Login / Signup | `login.html` | Public | Login, signup, and role-locked registration. |
| Role Selection | `select_role.html` | Public | Select admin or responder before registration. |
| Admin Dashboard | `home.html` | Admin | Active alerts, camera count, events today, average model detection time, recent events. |
| Test Model | `detect.html` | Admin | Video upload detection and browser camera detection. |
| Alert Management | `alerts.html` | Admin | Incident cards, evidence preview, approve/send, false alarm. |
| Responder Dashboard | `respond.html` | Responder | Assigned alerts, pending counts, response status chart. |
| Responder Alerts | `responder_alert.html` | Responder | Assigned case list, detail view, status updates, image enlargement. |

The shared `sidebar.html` changes navigation by role:

| Role | Sidebar Items |
| --- | --- |
| Admin | Dashboard, Alerts, Test Model, Logout |
| Responder | Dashboard, Responder Alerts, Logout |

---

## 🚨 Alert Lifecycle

```mermaid
stateDiagram-v2
    [*] --> new: Snapshot inserted
    new --> sent_to_responder: Admin approves
    new --> false_alarm: Admin marks false alarm
    sent_to_responder --> responded: Responder acknowledges / en route / on scene
    responded --> closed: Responder resolves
    sent_to_responder --> closed: Responder resolves directly
    false_alarm --> [*]
    closed --> [*]
```

| State | Stored Value | Visible To | Meaning |
| --- | --- | --- | --- |
| New | `new` or legacy empty status with `notified = 0` | Admin | Detection saved and waiting for review. |
| Active | `sent_to_responder` | Admin + Responder | Admin approved and dispatched the alert. |
| Responded | `responded` | Admin + Responder | A responder has accepted/progressed the case. |
| Closed | `closed` | Admin + Responder | The responder resolved the case. |
| False Alarm | `false_alarm` | Hidden from live queues | Admin dismissed it before dispatch. |

### Responder Status Values

| Value | UI Label |
| --- | --- |
| `pending` | Pending |
| `acknowledged` | Acknowledged |
| `en_route` | En Route |
| `on_scene` | On Scene |
| `resolved` | Resolved |

---

## 🗄️ Database Usage

The application uses SQLite through `accivision.db`. On startup, `init_db()` creates missing tables and adds missing columns with non-destructive `ALTER TABLE` migrations.

### `users`

| Column | Purpose |
| --- | --- |
| `id` | Auto-incrementing user ID. |
| `email` | Unique login email. |
| `password` | Werkzeug hash; legacy SHA/plain values can be upgraded after successful login. |
| `role` | `admin` or `responder`. |
| `created_at` | Creation timestamp. |

### `accidents`

| Column | Purpose |
| --- | --- |
| `id` | Short UUID-based incident ID. |
| `image` | Snapshot filename in `static/accidents/`. |
| `timestamp` | Unix timestamp when the snapshot was captured. |
| `notified` | Whether the alert was sent to responders. |
| `responded` | Whether a responder has acted on it. |
| `closed` | Whether the incident is resolved. |
| `status` | Main lifecycle state. |
| `sent_at`, `reported_at` | Dispatch timestamps. |
| `responded_at` | First responder action time. |
| `closed_at` | Resolution time. |
| `response_status` | Responder progress value. |
| `assigned_responder` | Responder email or `Responder Team`. |
| `confidence` | Schema field for confidence; current insert path leaves it at default unless pre-existing rows include data. |
| `source_video` | Schema field for source video; current insert path does not populate it. |
| `detection_time_seconds` | Schema field for inference timing; current insert path does not populate it. |

---

## 🧩 Route Map

| Route | Methods | Access | Description |
| --- | --- | --- | --- |
| `/`, `/intro.html` | GET | Public | Intro page. |
| `/login`, `/login.html` | GET, POST | Public | Login and signup form. |
| `/select-role`, `/select_role.html` | GET, POST | Public | Role selection before signup. |
| `/register/admin` | GET | Public | Locks signup role to admin. |
| `/register/responder` | GET | Public | Locks signup role to responder. |
| `/logout` | GET | Logged in | Clears session and returns to intro. |
| `/home` | GET | Logged in | Role-aware dashboard redirect. |
| `/dashboard`, `/home.html` | GET | Admin | Admin dashboard. |
| `/detect`, `/detect.html` | GET | Admin | Detection workspace. |
| `/alerts`, `/alerts.html` | GET | Admin | Alert management. |
| `/responder` | GET | Responder | Responder dashboard. |
| `/responder/alerts`, `/responder_alert.html` | GET | Responder | Responder alert list. |
| `/responder/alert/<accident_id>` | GET | Responder | Responder alert detail. |
| `/upload` | POST | Admin | Upload and validate video file. |
| `/video_feed` | GET | Admin | Multipart processed video stream. |
| `/camera_feed` | GET | Admin | Server-side camera stream from camera index `0`. |
| `/process_camera_frame` | POST | Admin | Process one browser camera frame. |
| `/stop_video` | POST | Admin | Stop upload processing and remove active upload. |
| `/stop_camera` | POST | Admin | Stop server-side camera flag. |
| `/accident_status` | GET | Admin | One-shot accident flag for warning audio. |
| `/report_alert/<accident_id>` | POST | Admin | Send incident to responders. |
| `/false_alarm/<accident_id>` | POST | Admin | Mark unreported incident as false alarm. |
| `/respond_alert/<accident_id>` | POST | Responder | Legacy/simple acknowledgement endpoint. |
| `/close_alert/<accident_id>` | POST | Responder | Legacy/simple close endpoint. |
| `/responder/update-status` | POST | Responder | Update responder status. |
| `/contact_authority` | POST | Logged in admin only | Legacy dispatch wrapper. |
| `/mark_responded` | POST | Logged in responder only | Legacy responder wrapper. |
| `/uploads/<filename>` | GET | Logged in | Serve uploaded media while available. |

---

## 📁 Project Structure

```text
accident_web/
├── app.py                         # Flask app, routes, auth, DB, detection pipeline
├── accivision.db                  # SQLite database
├── best.pt                        # Custom YOLO model
├── coco.txt                       # Detection label lookup loaded by app.py
├── requirements.txt               # Python dependencies
├── README.md                      # Project documentation
├── cloudflared-windows-amd64.exe  # Bundled tunnel executable, not called by app.py
├── templates/
│   ├── intro.html                 # Public introduction page
│   ├── login.html                 # Login and signup form
│   ├── select_role.html           # Role selection
│   ├── sidebar.html               # Shared role-aware navigation
│   ├── home.html                  # Dashboard layout
│   ├── detect.html                # Admin detection workspace
│   ├── alerts.html                # Admin alert management
│   ├── respond.html               # Responder dashboard
│   └── responder_alert.html       # Responder list/detail workspace
├── static/
│   ├── css/
│   │   └── style.css              # Application styling
│   ├── assets/
│   │   └── warning.mp3            # Browser warning sound
│   └── accidents/
│       └── accident_*.jpg         # Saved detection snapshots
└── uploads/
    └── uploaded videos            # Temporary uploaded media during analysis
```

---

## ⚙️ Installation & Run

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The local development server starts on:

```text
http://localhost:5000
```

Required runtime files in the project root:

| File | Required For |
| --- | --- |
| `best.pt` | YOLO accident detection model. |
| `coco.txt` | Class label lookup. |
| `accivision.db` | SQLite storage; created/migrated by the app if needed. |

---

## 🧪 Current Dependencies

```text
flask
opencv-python
ultralytics
numpy
werkzeug
```

---

## 🔐 Authentication & Access Control

AcciVision uses Flask sessions and role decorators:

| Decorator | Protects |
| --- | --- |
| `login_required` | Any authenticated route. |
| `admin_required` | Detection, admin dashboard, upload, alerts, dispatch, false alarm. |
| `responder_required` | Responder dashboard, responder alert details, response updates. |

Passwords are created with Werkzeug password hashing. The verifier also supports legacy SHA-256 or plain stored values and upgrades them to Werkzeug hashes after a successful login.

---

## ✅ Implementation Notes

- The YOLO model is loaded once globally: `YOLO(os.path.join(BASE_DIR, "best.pt"))`.
- `coco.txt` supplies the class label lookup; `app.py` treats only the `accident` label as alert-triggering.
- Browser-camera detection sends JPEG/base64 frame payloads to Flask through `/process_camera_frame`.
- Uploaded-video frames are streamed back as `multipart/x-mixed-replace`.
- Warning sound is polling-based, not WebSocket-based.
- Saved accident snapshots are throttled to reduce duplicate evidence during continuous detections.
- False alarms remain in the database but are hidden from live admin and responder queues.
- Locations shown in dashboards are generated display labels from the accident ID, not GPS coordinates.

---

<div align="center">

### Built for practical AI-assisted traffic incident monitoring

`Flask` · `YOLOv8` · `OpenCV` · `SQLite` · `Admin Dashboard` · `Responder Workflow`

</div>

## Team Members

- [Amal Alharbi](https://github.com/Amal-K-Alharbi)
- [Hams Algusuni](https://github.com/Hamse1Ai)
- [Sarah Faisal](https://github.com/Fai-Aqlah)
- [Fai Aqlah](https://github.com/isarahfaisal)
- [Mariyyah Fahad](https://github.com/Mariyyah1F)
