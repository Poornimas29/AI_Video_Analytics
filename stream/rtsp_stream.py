# stream/rtsp_stream.py
"""RTSP video stream wrapper.

Provides a lightweight class that opens an RTSP URL with OpenCV, attempts
reconnection on failure, and yields frames together with timestamps.
Supports a premium synthetic mock mode when the URL is set to "mock".
"""

from __future__ import annotations

import cv2
import os
import re
import time
import numpy as np
from datetime import datetime, timedelta
from typing import Tuple

import config.settings as settings


def _extract_start_datetime(filename: str) -> datetime:
    """Extract start timestamp from video filename (e.g. 20260727064507), or fallback to now."""
    basename = os.path.basename(filename)
    m = re.search(r'(20\d{2})(\d{2})(\d{2})_?(\d{2})(\d{2})(\d{2})', basename)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6))
            )
        except ValueError:
            pass
    return datetime.utcnow()


class RTSPStream:
    """Handle a single RTSP camera.

    Parameters
    ----------
    cam_id: str
        Identifier for the camera (used for logging and output).
    url: str
        RTSP URL.
    reconnect_delay: int, optional
        Seconds to wait before trying to reconnect after a failure.
    """

    def __init__(self, cam_id: str, url: str, reconnect_delay: int = 5):
        self.cam_id = cam_id
        self.url = url
        self.reconnect_delay = reconnect_delay
        self.cap: cv2.VideoCapture | None = None
        self._stop = False
        self._frame_interval: float = 0.0   # seconds between frames for file playback
        self._last_frame_time: float = 0.0  # monotonic clock of last decoded frame
        self._start_datetime: datetime | None = None

        self.is_mock = (url.lower() == "mock")
        # Detect local file playback (not RTSP and not mock)
        self._is_file = (
            not self.is_mock
            and not url.lower().startswith("rtsp://")
        )
        self.is_eof = False

    def _open(self) -> None:
        if self.is_mock:
            return

        # Open RTSP using FFMPEG backend.
        # Set stimeout (socket timeout) to 5 seconds so a dead RTSP host fails
        # quickly instead of blocking the reader thread for 20–30 seconds.
        # The option is embedded in the RTSP URL as an FFMPEG AVOption.
        timeout_url = self.url
        # Only modify URL if it is an RTSP stream.
        if timeout_url.lower().startswith('rtsp://'):
            if "?" in timeout_url:
                timeout_url = self.url + "&timeout=5000000"  # 5s in microseconds
            else:
                timeout_url = self.url + "?timeout=5000000"
        # For local file paths (e.g., playback video), use the original URL.
        self.cap = cv2.VideoCapture(timeout_url, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            # Fallback: try without the custom timeout URL
            self.cap = cv2.VideoCapture(self.url)
        if not self.cap.isOpened():
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            raise RuntimeError(f"[{self.cam_id}] Unable to open RTSP stream: {self.url}")
        # Set video buffer size to 1 to enforce real-time decoding and prevent queue buildup lag
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # For local file playback, read native FPS and initialize speed
        self._native_fps = 25.0
        self.speed: float = float(getattr(settings, "PLAYBACK_SPEED", 1.0))
        self._is_paused: bool = False
        if self._is_file:
            self._start_datetime = _extract_start_datetime(self.url)
            n_fps = self.cap.get(cv2.CAP_PROP_FPS)
            if n_fps and n_fps > 0:
                self._native_fps = n_fps
            self._update_interval()
            self._last_frame_time = 0.0

    def _update_interval(self) -> None:
        if self.speed > 0 and not self._is_paused:
            # We enforce a display interval corresponding to the native FPS (e.g. 25-30fps target)
            self._frame_interval = (1.0 / self._native_fps) / min(self.speed, 2.0)
        else:
            self._frame_interval = 0.0

    def set_speed(self, speed: float) -> None:
        """Dynamically set playback speed multiplier (e.g. 1.0, 5.0, 10.0, or 0.0 for pause)."""
        if speed <= 0.0:
            self._is_paused = True
        else:
            self._is_paused = False
            self.speed = float(speed)
        self._update_interval()

    def read(self) -> Tuple[bool, "any", datetime]:
        """Read a single frame.

        Returns
        -------
        tuple
            ``(success, frame, timestamp)`` where ``timestamp`` is a ``datetime``.
        """
        if self.is_mock:
            return self._read_mock()

        if self.cap is None or not self.cap.isOpened():
            self._open()

        # If paused, sleep briefly and return last state
        if self._is_file and self._is_paused:
            time.sleep(0.05)
            ret, frame = self.cap.read()
            # Rewind by 1 frame so pause stays on same frame
            if ret and self.cap.isOpened():
                cur = self.cap.get(cv2.CAP_PROP_POS_FRAMES)
                if cur > 1:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, cur - 1)
            frame_ts = self._start_datetime or datetime.utcnow()
            return True, frame, frame_ts

        # Frame skip for high speeds (e.g. 3x, 5x, 10x, 20x) so video advances
        # smoothly without overloading CPU decoder
        if self._is_file and self.speed >= 2.0:
            skip_count = int(self.speed) - 1
            for _ in range(skip_count):
                if not self.cap.grab():
                    break

        # Throttle file playback
        if self._is_file and self._frame_interval > 0:
            now = time.monotonic()
            elapsed = now - self._last_frame_time
            if elapsed < self._frame_interval:
                time.sleep(self._frame_interval - elapsed)
            self._last_frame_time = time.monotonic()

        ret, frame = self.cap.read()
        if not ret or frame is None or frame.size == 0:
            if self._is_file:
                self.is_eof = True
                logger.info("[%s] Video file playback reached end of stream.", self.cam_id)
                return False, None, None
            else:
                self.cap.release()
                self.cap = None
                raise RuntimeError(f"[{self.cam_id}] Frame read failed")

        if self._is_file and self._start_datetime:
            pos_msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            frame_ts = self._start_datetime + timedelta(milliseconds=pos_msec)
        else:
            frame_ts = datetime.utcnow()

        return True, frame, frame_ts

    def _read_mock(self) -> Tuple[bool, np.ndarray, datetime]:
        """Generate a simulated camera frame for mock testing."""
        w, h = 640, 360
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        
        # 1. Premium dark-green grid background
        for x in range(0, w, 40):
            cv2.line(frame, (x, 0), (x, h), (12, 22, 12), 1)
        for y in range(0, h, 40):
            cv2.line(frame, (0, y), (w, y), (12, 22, 12), 1)
            
        # 2. Radar sweep graphics
        cx, cy = w // 2, h // 2
        r = 150
        cv2.circle(frame, (cx, cy), r, (0, 60, 0), 2)
        cv2.circle(frame, (cx, cy), r // 2, (0, 40, 0), 1)
        
        # Calculate sweeping line angle
        t = time.time()
        angle = (t * 1.5) % (2 * np.pi)
        
        end_x = int(cx + r * np.cos(angle))
        end_y = int(cy + r * np.sin(angle))
        cv2.line(frame, (cx, cy), (end_x, end_y), (0, 180, 0), 2)
        
        # 3. Draw walking targets corresponding to YOLO26Detector simulation bboxes
        tx1 = int((w * 0.3) + (w * 0.1) * np.sin(t * 0.4))
        ty1 = int((h * 0.55) + 20 * np.cos(t * 0.4))
        
        # Draw target indicators
        cv2.circle(frame, (tx1, ty1), 6, (0, 255, 0), -1)
        cv2.circle(frame, (tx1, ty1), 12, (0, 255, 0), 1)
        
        tx2 = int(w * 0.74)
        ty2 = int(h * 0.55)
        cv2.circle(frame, (tx2, ty2), 6, (0, 255, 0), -1)
        
        # 4. Premium HUD Label
        cv2.putText(frame, f"LIVE MOCK - {self.cam_id}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)
        
        # Simulate frame timing (25 FPS -> 40ms)
        time.sleep(0.04)
        
        return True, frame, datetime.utcnow()

    def release(self) -> None:
        self._stop = True
        if self.cap is not None:
            self.cap.release()
            self.cap = None
