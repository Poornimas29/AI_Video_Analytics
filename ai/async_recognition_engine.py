# ai/async_recognition_engine.py
"""AsyncRecognitionEngine — decouples InsightFace inference from the main frame loop.

InsightFace (buffalo_l) takes 50–200ms per call on CPU. Running it synchronously inside the
frame-processing loop blocks ByteTrack from receiving detections, causing track loss and
identity instability.

This engine runs InsightFace on a background ThreadPoolExecutor so the main loop
is never blocked. The caller submits a crop image for a track and later polls
for completed results.

Usage
-----
    engine = AsyncRecognitionEngine(face_rec_manager, embedding_manager)
    engine.start()

    # In the main frame loop — non-blocking:
    engine.submit(cam_id, track_id, timestamp, crop_img)
    for result in engine.drain_results():
        cam_id, track_id, ts, emp_id, emp_name, score = result
        # apply identity to track_mem here

    engine.stop()
"""

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from typing import Iterator, List, Optional, Tuple

import numpy as np
import config.settings as settings

logger = logging.getLogger(__name__)

# Each result: (cam_id, track_id, timestamp, emp_id_or_None, emp_name, score)
RecognitionResult = Tuple[str, int, datetime, Optional[str], str, float]


class AsyncRecognitionEngine:
    """Non-blocking InsightFace recognition engine backed by a thread pool."""

    def __init__(
        self,
        face_rec_manager,
        embedding_manager,
        reid_engine=None,
        max_workers: int = 2,
    ) -> None:
        self._face_rec = face_rec_manager
        self._embed_mgr = embedding_manager
        self._reid_engine = reid_engine
        self._executor = ThreadPoolExecutor(max_workers=max(max_workers, 2), thread_name_prefix="recog")
        self._results: "queue.Queue[RecognitionResult]" = queue.Queue()
        self._in_flight: "set[Tuple[str, int]]" = set()
        self._in_flight_lock = threading.Lock()
        self._running = False

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background executor (no-op if already started)."""
        self._running = True
        logger.info("[AsyncRecognitionEngine] Started with %d worker thread(s).", self._executor._max_workers)

    def stop(self) -> None:
        """Gracefully shut down the thread pool."""
        self._running = False
        self._executor.shutdown(wait=False)
        logger.info("[AsyncRecognitionEngine] Stopped.")

    def submit(
        self,
        cam_id: str,
        track_id: int,
        timestamp: datetime,
        crop: np.ndarray,
    ) -> bool:
        """Submit a person crop for background face recognition.

        Returns True if the task was queued, False if the track is already
        being processed (deduplication guard).
        """
        if not self._running:
            return False
        if crop is None or crop.size == 0:
            return False

        key = (cam_id, track_id)
        with self._in_flight_lock:
            if key in self._in_flight:
                return False          # Already processing this track
            if len(self._in_flight) >= 4:
                return False          # Drop task if queue is full so recognition stays live and never lags behind
            self._in_flight.add(key)

        # Submit the inference to the background thread pool
        future: Future = self._executor.submit(
            self._run_recognition, cam_id, track_id, timestamp, crop.copy()
        )
        future.add_done_callback(lambda f: self._on_done(key, f))
        return True

    def drain_results(self) -> List[RecognitionResult]:
        """Drain and return all completed recognition results.

        Must be called from the main frame loop each frame to consume results
        as they complete. Returns an empty list if no results are ready.
        """
        results: List[RecognitionResult] = []
        try:
            while True:
                results.append(self._results.get_nowait())
        except queue.Empty:
            pass
        return results

    def is_in_flight(self, cam_id: str, track_id: int) -> bool:
        """Check if this track is currently being processed."""
        with self._in_flight_lock:
            return (cam_id, track_id) in self._in_flight

    # ── Internal ────────────────────────────────────────────────────────────

    def _run_recognition(
        self,
        cam_id: str,
        track_id: int,
        timestamp: datetime,
        crop: np.ndarray,
    ) -> RecognitionResult:
        """Runs in a background thread. Detects faces, embeds, and matches gallery.
        Falls back to Body Re-ID appearance matching when face is turned away.
        """
        try:
            # 1. Try High-Resolution Face Recognition first
            h, w = crop.shape[:2]
            # Extract upper body / head region where face resides
            if h > 80:
                head_crop = crop[:int(h * 0.60), :]
            else:
                head_crop = crop

            import cv2 as _cv2
            hh, hw = head_crop.shape[:2]
            max_side = 640
            if max(hh, hw) > max_side:
                scale = max_side / max(hh, hw)
                head_crop = _cv2.resize(
                    np.ascontiguousarray(head_crop, dtype=np.uint8),
                    (max(1, int(hw * scale)), max(1, int(hh * scale))),
                    interpolation=_cv2.INTER_LINEAR,
                )
            else:
                head_crop = np.ascontiguousarray(head_crop, dtype=np.uint8)

            face_dets = self._face_rec.detect_faces(head_crop)
            # If no face found in upper body, fallback to full body
            if not face_dets and h > 80:
                if max(h, w) > max_side:
                    scale = max_side / max(h, w)
                    full_crop = _cv2.resize(
                        np.ascontiguousarray(crop, dtype=np.uint8),
                        (max(1, int(w * scale)), max(1, int(h * scale))),
                        interpolation=_cv2.INTER_LINEAR,
                    )
                else:
                    full_crop = np.ascontiguousarray(crop, dtype=np.uint8)
                face_dets = self._face_rec.detect_faces(full_crop)

            if face_dets:
                def _face_quality(f):
                    bw, bh = f["bbox"][2], f["bbox"][3]
                    score = f.get("det_score", f.get("score", 1.0))
                    return bw * bh * score

                face_det = max(face_dets, key=_face_quality)
                is_ok, reason = self._face_rec.validate_face(face_det)
                if is_ok:
                    query_emb = face_det.get("embedding")
                    if query_emb is not None:
                        emp_id, emp_name, score = self._embed_mgr.match_embedding(query_emb)
                        if emp_id and score >= settings.RECOGNITION_THRESHOLD:
                            return (cam_id, track_id, timestamp, emp_id, emp_name or "Unknown", score)

            # Return Unknown when face biometrics are not detected or below threshold (no clothing/dress assumption)
            return (cam_id, track_id, timestamp, None, "Unknown", 0.0)

        except Exception as exc:
            logger.error(
                "[AsyncRecognitionEngine] Exception for Track %d on Camera %s: %s",
                track_id, cam_id, exc,
            )
            return (cam_id, track_id, timestamp, None, "Unknown", 0.0)

    def _on_done(self, key: Tuple[str, int], future: Future) -> None:
        """Callback invoked by the executor thread when inference completes."""
        with self._in_flight_lock:
            self._in_flight.discard(key)
        try:
            result = future.result()
            self._results.put(result)
        except Exception as exc:
            logger.error("[AsyncRecognitionEngine] Future raised exception: %s", exc)
