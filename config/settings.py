"""
Configuration settings module for the Employee Monitoring System.
Loads environment variables from a .env file and provides typed settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root — override=True ensures .env always wins even if
# environment variables were cached from a previous run.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)


# RTSP Stream Settings
RTSP_URL: str = os.getenv("RTSP_URL", "mock")
RTSP_HOST: str = os.getenv("RTSP_HOST", "")
RTSP_USERNAME: str = os.getenv("RTSP_USERNAME", "")
RTSP_PASSWORD: str = os.getenv("RTSP_PASSWORD", "")
try:
    RTSP_PORT: int = int(os.getenv("RTSP_PORT", "0"))
except ValueError:
    RTSP_PORT = 0


# Reconnection interval in seconds
RECONNECT_INTERVAL: int = int(os.getenv("RECONNECT_INTERVAL", "5"))

TARGET_FPS: int = int(os.getenv("TARGET_FPS", "30"))

try:
    PLAYBACK_SPEED: float = float(os.getenv("PLAYBACK_SPEED", "1.0"))
except ValueError:
    PLAYBACK_SPEED = 1.0

# Storage Directories
CAPTURE_DIR: str = os.getenv("CAPTURE_DIR", "captures")
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
LOG_FILE: str = os.path.join(LOG_DIR, "app.log")

# Camera Configuration List
CAMERAS: list[dict] = [
    {
        "id": "CAM001",
        "name": "Main Entrance Camera",
        "channel": 1,           # NVR Channel number
        "enabled": True,
        "is_entrance": True,
        "url": "/Users/poornima/Video Analytics/Vel Videos/Vel New_ch1_20260727064511_20260727090051.mp4"
    },
    {
        "id": "CAM002",
        "name": "Kitchen Entrance Camera",
        "channel": 13,          # NVR Channel number
        "enabled": True,
        "is_entrance": True,
        "url": "/Users/poornima/Video Analytics/Vel Videos/Vel New_ch13_20260727064507_20260727090051.mp4"
    },
    # To add a 3rd and 4th camera, simply add more dictionaries:
    {
        "id": "CAM003",
        "name": "Main Inner Camera",
        "channel": 7,
        "enabled": True,
        "is_entrance": False,
        "url": "/Users/poornima/Video Analytics/Vel Videos/Vel New_ch7_20260727064507_20260727090051.mp4"
    },
    {
        "id": "CAM004",
        "name": "Kitchen Inner Camera",
        "channel": 4,
        "enabled": True,
        "is_entrance": False,
        "url": "/Users/poornima/Video Analytics/Vel Videos/Vel New_ch12_20260727064515_20260727090051.mp4"
    }
]

# Designated Entrance/Exit Camera IDs
_raw_entrance_cams = os.getenv("ENTRANCE_CAMERAS", "CAM001,CAM002")
ENTRANCE_CAMERA_IDS: list[str] = [
    c.strip().upper() for c in _raw_entrance_cams.split(",") if c.strip()
]


def is_entrance_camera(camera_id: str | None) -> bool:
    """Return True if the camera is configured as a designated Entrance/Exit camera."""
    if not camera_id:
        return False
    cid_upper = str(camera_id).strip().upper()
    if cid_upper in ENTRANCE_CAMERA_IDS:
        return True
    # Check in CAMERAS dictionary
    for cam in CAMERAS:
        if str(cam.get("id", "")).strip().upper() == cid_upper:
            if cam.get("is_entrance"):
                return True
            if "entrance" in str(cam.get("name", "")).lower():
                return True
    return False


def get_camera_type(camera_id: str | None) -> str:
    """Return 'entrance' or 'inner' depending on the camera's designated role."""
    return "entrance" if is_entrance_camera(camera_id) else "inner"


# Phase 2 Configs
try:
    CONF_PERSON: float = float(os.getenv("CONF_PERSON", "0.50"))
except ValueError:
    CONF_PERSON = 0.50

try:
    CONF_PHONE: float = float(os.getenv("CONF_PHONE", "0.30"))
except ValueError:
    CONF_PHONE = 0.30

try:
    CONF_UNIFORM: float = float(os.getenv("CONF_UNIFORM", "0.40"))
except ValueError:
    CONF_UNIFORM = 0.40

try:
    CONF_SAFETY_CAP: float = float(os.getenv("CONF_SAFETY_CAP", "0.40"))
except ValueError:
    CONF_SAFETY_CAP = 0.40

try:
    PHONE_USAGE_CONFIRM_SECONDS: float = float(os.getenv("PHONE_USAGE_CONFIRM_SECONDS", "2.0"))
except ValueError:
    PHONE_USAGE_CONFIRM_SECONDS = 2.0

try:
    REID_SIMILARITY_THRESHOLD: float = float(os.getenv("REID_SIMILARITY_THRESHOLD", "0.65"))
except ValueError:
    REID_SIMILARITY_THRESHOLD = 0.65

# ─── Face Recognition, Tracking & Session Settings ─────────────────────────
try:
    RECOGNITION_INTERVAL: float = float(os.getenv("RECOGNITION_INTERVAL", "1.0"))
except ValueError:
    RECOGNITION_INTERVAL = 1.0

try:
    RECOGNITION_THRESHOLD: float = float(os.getenv("RECOGNITION_THRESHOLD", "0.50"))
except ValueError:
    RECOGNITION_THRESHOLD = 0.50

try:
    MIN_FACE_SIZE: int = int(os.getenv("MIN_FACE_SIZE", "15"))
except ValueError:
    MIN_FACE_SIZE = 15

try:
    MIN_FACE_QUALITY: float = float(os.getenv("MIN_FACE_QUALITY", "1.0"))
except ValueError:
    MIN_FACE_QUALITY = 1.0

try:
    MIN_CONSECUTIVE_MATCHES: int = int(os.getenv("MIN_CONSECUTIVE_MATCHES", "3"))
except ValueError:
    MIN_CONSECUTIVE_MATCHES = 3

try:
    TRACK_TIMEOUT: float = float(os.getenv("TRACK_TIMEOUT", "60.0"))
except ValueError:
    TRACK_TIMEOUT = 60.0

# MAX_RECOGNITION_ATTEMPTS intentionally removed. Recognition retries until
# identity is locked. Permanently giving up on a visible track is a bug, not a feature.

try:
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.50"))
except ValueError:
    SIMILARITY_THRESHOLD = 0.50

try:
    UNKNOWN_TRACK_CLEANUP_MINUTES: float = float(os.getenv("UNKNOWN_TRACK_CLEANUP_MINUTES", "15.0"))
except ValueError:
    UNKNOWN_TRACK_CLEANUP_MINUTES = 15.0


def get_playback_url_for_camera(camera_id: any = "") -> str | None:
    """Return the per-camera playback URL if defined, else None."""
    if camera_id is not None and str(camera_id).strip():
        cam_key = str(camera_id).upper()
        val = os.getenv(f"PLAYBACK_URL_{cam_key}")
        if val:
            return val
    return os.getenv("PLAYBACK_URL")


def build_default_rtsp_url(camera_id: any = "", channel: int = 1) -> str:
    """Construct a full RTSP URL from the individual environment variables.

    If ``RTSP_URL`` is set to something other than the literal ``"mock"`` it is
    returned unchanged.  Otherwise we compose ``rtsp://[user[:pass]@]host:port``.
    Empty username/password parts are omitted cleanly.
    """
    enable_playback = os.getenv("ENABLE_PLAYBACK", "0") not in ("0", "false", "False")
    playback_url = get_playback_url_for_camera(camera_id)
    if enable_playback and playback_url:
        return playback_url

    if RTSP_URL and RTSP_URL.lower() == "mock":
        return "mock"

    if not RTSP_HOST or RTSP_HOST.lower() == "mock":
        return "mock"

    auth = ""
    if RTSP_USERNAME and RTSP_PASSWORD:
        import urllib.parse
        encoded_pass = urllib.parse.quote(RTSP_PASSWORD)
        auth = f"{RTSP_USERNAME}:{encoded_pass}@"
    elif RTSP_USERNAME:
        auth = f"{RTSP_USERNAME}@"

    port_str = f":{RTSP_PORT}" if RTSP_PORT else ""
    return f"rtsp://{auth}{RTSP_HOST}{port_str}/cam/realmonitor?channel={channel}&subtype=0"
