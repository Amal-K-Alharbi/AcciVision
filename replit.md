# AcciVision — Replit Setup

## Overview
AcciVision is a Flask-based real-time accident detection system that uses a YOLOv8 computer vision model to identify accidents in video streams (uploaded files or live camera feeds). It provides role-based dashboards (admin / responder), evidence capture, and an end-to-end alert lifecycle.

## Stack
- **Backend / Web**: Flask 3 + flask-sock (single process serves both API and HTML templates)
- **ML**: Ultralytics YOLOv8 (CPU-only PyTorch build) — model weights at `best.pt`
- **Computer Vision**: OpenCV (headless build, since the Replit container has no display server)
- **Database**: SQLite (`accivision.db`) — auto-initialized on startup
- **Frontend**: Server-rendered HTML templates in `templates/` with vanilla JS and CSS in `static/`

## Project Layout
- `app.py` — Flask application (routes, auth, model inference, streaming, alert lifecycle)
- `templates/` — Jinja2 templates (intro, login, home, detect, alerts, respond, sidebar)
- `static/` — CSS, JS, assets, captured accident snapshots
- `uploads/` — Uploaded video files
- `best.pt` — YOLOv8 trained accident-detection weights
- `coco.txt` — Class label list
- `accivision.db` — SQLite database file
- `requirements.txt` — Python dependency list

## Replit Environment Configuration
- **Python**: 3.12 (provided by the `python-3.12` Nix module in `.replit`)
- **Workflow**: `Start application` runs `python app.py` and binds to port **5000** (webview).
- **Host**: Flask binds to `0.0.0.0:5000`; the default port was changed from 10000 to 5000 so the Replit web preview can serve it as a webview workflow.
- **PyTorch**: Installed as the CPU-only wheel from `https://download.pytorch.org/whl/cpu` to stay within the disk quota (the default CUDA build pulls ~3 GB of NVIDIA libraries that are unusable in this CPU-only container).
- **OpenCV**: `opencv-python-headless` only (the GUI build pulls in `libxcb` which is not present on NixOS by default and breaks ultralytics imports).

## Running Locally on Replit
The `Start application` workflow handles this automatically:
```
python app.py
```
The app listens on `0.0.0.0:5000` and is served via the Replit web preview.

## Deployment
Configured for **VM** deployment (the app keeps process-local state — SQLite file, in-memory alert flags, local snapshot files — so a single always-on instance is required):
```
gunicorn --bind=0.0.0.0:5000 --workers=1 --threads=8 --timeout=300 app:app
```

## Notes / Gotchas
- The repo previously contained a `cloudflared-windows-amd64.exe` (~66 MB) that was unused on Linux/Replit; it has been removed.
- Live camera (`/camera_feed`) requires a server-attached webcam, which is not available in the Replit container — uploaded video / browser camera flows are the supported paths in this environment.
- The `best.pt` YOLO weights file (~22 MB) is committed to the repo and loaded once at process startup.
