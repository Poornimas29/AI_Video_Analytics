import os
import sys

# Ensure project root is in sys.path for running in isolated/embedded Python environments
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import datetime
import logging
import time
from typing import Optional, List
import cv2
import numpy as np


from config.logging_config import setup_logging
from config.settings import CAPTURE_DIR, RECONNECT_INTERVAL, RTSP_HOST, RTSP_URL, TARGET_FPS
from employee_management.employee_manager import EmployeeManager
from stream.stream_manager import StreamManager
from stream.camera_manager import CameraManager
from stream.grid_renderer import GridRenderer
from ai.face_recognition import FaceRecognitionEngine
from ai.session_engine import EmployeeSessionEngine

# Initialize logging configuration
setup_logging()
logger = logging.getLogger("main")


def make_offline_frame() -> np.ndarray:
    """
    Generates a high-quality offline placeholder frame when there is no camera signal.

    Returns:
        NumPy array representing a 1280x720 gray/black offline frame with visual grid.
    """
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    # Draw dark blue-grey styling grid
    for x in range(0, 1280, 80):
        cv2.line(frame, (x, 0), (x, 720), (25, 25, 30), 1)
    for y in range(0, 720, 80):
        cv2.line(frame, (0, y), (1280, y), (25, 25, 30), 1)

    # Draw visual warning sign
    cv2.circle(frame, (640, 300), 50, (0, 0, 150), -1)
    cv2.circle(frame, (640, 300), 45, (0, 165, 255), 3)
    cv2.putText(
        frame, "!", (630, 320),
        cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4, cv2.LINE_AA
    )

    # Central offline status text
    cv2.putText(
        frame, "CAMERA SIGNAL OFFLINE", (430, 400),
        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 220), 2, cv2.LINE_AA
    )
    cv2.putText(
        frame, f"Attempting connection every {RECONNECT_INTERVAL}s...", (425, 440),
        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1, cv2.LINE_AA
    )

    return frame


def draw_overlay(
    frame: np.ndarray,
    status: str,
    fps: float,
    width: int,
    height: int,
    connected: bool,
    camera_name: str = "Camera"
) -> np.ndarray:
    """
    Draws a premium styled semi-transparent status overlay on the video frame.

    Args:
        frame: The raw video frame.
        status: Status text (e.g., 'Connected', 'Disconnected').
        fps: Current streaming frame rate.
        width: Frame width.
        height: Frame height.
        connected: Boolean indicating if the camera is online.

    Returns:
        Frame with the overlay drawn on it.
    """
    frame_h, frame_w = frame.shape[:2]

    # Create top bar region copy for alpha blending
    overlay_bar = frame.copy()
    bar_height = 100
    cv2.rectangle(overlay_bar, (0, 0), (frame_w, bar_height), (15, 12, 10), -1)

    # Alpha blending: 60% opacity for status bar
    alpha = 0.6
    cv2.addWeighted(overlay_bar, alpha, frame, 1.0 - alpha, 0, frame)

    # Status color (Green = Online, Orange/Red = Offline)
    status_color = (40, 220, 40) if connected else (40, 40, 240)

    # Get current formatted timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Column 1: Connection Status & FPS
    cv2.putText(
        frame, f"{camera_name} : {status}", (25, 35),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, status_color, 2, cv2.LINE_AA
    )
    cv2.putText(
        frame, f"FPS    : {fps:.1f}", (25, 70),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 2, cv2.LINE_AA
    )

    # Column 2: Resolution & Current Date-Time
    cv2.putText(
        frame, f"Resolution : {width} x {height}", (320, 35),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 2, cv2.LINE_AA
    )
    cv2.putText(
        frame, f"Time       : {timestamp}", (320, 70),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 2, cv2.LINE_AA
    )

    # Column 3: Quick Key Controls Help (Right-aligned layout)
    cv2.putText(
        frame, "Controls:", (frame_w - 380, 35),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 1, cv2.LINE_AA
    )
    cv2.putText(
        frame, "[S] Save Snapshot | [Q] Quit Window", (frame_w - 380, 70),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA
    )

    return frame


def main() -> None:
    """Main application orchestrator."""
    logger.info("Application Started")

    # Instantiate the Camera Manager coordinating multiple streams
    manager = CameraManager(reconnect_interval=RECONNECT_INTERVAL)

    # Retrieve all configured enabled cameras
    cameras = manager.get_active_cameras()

    # Fallback to single camera if no cameras registry exists
    if not cameras:
        if RTSP_HOST:
            # Reconstruct single camera config dynamically to preserve backwards compatibility
            cameras = [{
                "id": "CAM001",
                "name": "CCTV Monitor",
                "channel": 1,
                "enabled": True
            }]
            manager.camera_configs = {"CAM001": cameras[0]}
        else:
            logger.error(
                "RTSP connection properties are not configured in settings or .env file."
            )
            print("\n[ERROR] No cameras or RTSP connection parameters configured in .env file.")
            return

    manager.start_all()

    from services.monitoring_service import MonitoringService
    monitoring_service = MonitoringService(display=False)
    print("----------------------------------")
    print("Persistent CCTV Analytics System Active")
    print("Continuous Body Tracking Enabled")
    print("----------------------------------")

    # Pre-compute stable lookup structures for the renderer
    camera_ids: List[str] = [cam["id"] for cam in cameras]
    camera_names: dict = {cam["id"]: cam["name"] for cam in cameras}

    # Initialise the grid renderer with a fixed cell size of 640 × 360 pixels
    renderer = GridRenderer(cell_size=(640, 360))
    rows, cols = renderer.grid_dimensions(len(camera_ids))

    # Single dashboard window
    DASHBOARD_TITLE = "CCTV Monitor - Live Dashboard"
    cv2.namedWindow(DASHBOARD_TITLE, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(
        DASHBOARD_TITLE,
        cols * 640,
        rows * 360 + GridRenderer.HEADER_HEIGHT,
    )

    # Snapshot flash message state: text + expiry timestamp
    flash_text: str = ""
    flash_expiry: float = 0.0

    last_processed_timestamps = {}
    annotated_frames = {}

    try:
        # ── Start Live Web Dashboard Server ──────────────────────────────
        import threading
        import uvicorn
        import web_dashboard.app as dashboard_app
        dashboard_app.set_shared_pipeline(manager, monitoring_service, renderer)

        def _run_web_server():
            try:
                uvicorn.run(dashboard_app.app, host="0.0.0.0", port=8000, log_level="warning")
            except Exception as e:
                logger.warning(f"Web server exception: {e}")

        web_thread = threading.Thread(target=_run_web_server, daemon=True)
        web_thread.start()
        logger.info("Live Web Dashboard running at http://localhost:8000")

        # ── Warm-up YOLO detector so bounding boxes appear from the first frame ──
        import numpy as _np
        _dummy = _np.zeros((360, 640, 3), dtype=_np.uint8)
        monitoring_service.detection_manager.detect(_dummy)
        logger.info("YOLO warm-up complete — starting display loop.")

        # Loop interval derived from target FPS and PLAYBACK_SPEED
        current_speed: float = float(os.getenv("PLAYBACK_SPEED", "1.0"))
        is_paused: bool = False
        enable_playback = os.getenv("ENABLE_PLAYBACK", "0") not in ("0", "false", "False")
        if enable_playback and current_speed > 1.0:
            wait_time_ms = max(1, int(1000 / (TARGET_FPS * current_speed)))
        else:
            wait_time_ms = max(1, int(1000 / TARGET_FPS))

        while True:
            connected_map = {cam_id: manager.is_connected(cam_id) for cam_id in camera_ids}
            fps_map = {cam_id: manager.get_fps(cam_id) for cam_id in camera_ids}
            frames = {}

            for cam_id in camera_ids:
                latest_data = manager.get_latest_frame_with_timestamp(cam_id)
                if latest_data is not None:
                    frame, ts = latest_data
                    last_ts = last_processed_timestamps.get(cam_id)
                    if ts != last_ts:
                        try:
                            annotated, person_states = monitoring_service.process_camera_frame(
                                cam_id, frame, ts
                            )
                            annotated_frames[cam_id] = annotated
                            last_processed_timestamps[cam_id] = ts
                            frames[cam_id] = annotated
                        except Exception as proc_exc:
                            logger.error(
                                f"Error processing frame for {cam_id}: {proc_exc}",
                                exc_info=True,
                            )
                            frames[cam_id] = annotated_frames.get(cam_id, frame)
                    else:
                        frames[cam_id] = annotated_frames.get(cam_id, frame)
                else:
                    frames[cam_id] = None

            # ── Build and display the single dashboard frame ──────────────────
            active_speed_display = 0.0 if is_paused else current_speed
            valid_ts = [ts for ts in last_processed_timestamps.values() if ts is not None]
            latest_video_ts = max(valid_ts) if valid_ts else None

            dashboard = renderer.build_dashboard(
                camera_ids=camera_ids,
                camera_names=camera_names,
                frames=frames,
                connected=connected_map,
                fps_map=fps_map,
                speed=active_speed_display,
                current_ts=latest_video_ts,
            )

            # Update composite grid JPEG for web stream
            try:
                _, g_buf = cv2.imencode(".jpg", dashboard, [cv2.IMWRITE_JPEG_QUALITY, 80])
                dashboard_app.state.latest_grid_jpeg = g_buf.tobytes()
            except Exception:
                pass

            # Snapshot confirmation flash (drawn over the rendered dashboard)
            if time.time() < flash_expiry and flash_text:
                dh, dw = dashboard.shape[:2]
                box_x1, box_y1 = dw // 2 - 260, dh - 72
                box_x2, box_y2 = dw // 2 + 260, dh - 24
                flash_overlay = dashboard.copy()
                cv2.rectangle(flash_overlay, (box_x1, box_y1), (box_x2, box_y2), (0, 0, 0), -1)
                cv2.addWeighted(flash_overlay, 0.72, dashboard, 0.28, 0, dashboard)
                cv2.putText(
                    dashboard, flash_text,
                    (box_x1 + 16, box_y2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 230, 0), 2, cv2.LINE_AA,
                )

            cv2.imshow(DASHBOARD_TITLE, dashboard)
            key = cv2.waitKey(wait_time_ms) & 0xFF

            # Detect window close via the X button
            try:
                if cv2.getWindowProperty(DASHBOARD_TITLE, cv2.WND_PROP_VISIBLE) < 1:
                    logger.info("Dashboard window closed by user.")
                    break
            except Exception:
                pass

            # ── Auto-exit when all video files complete playback ─────────────
            if manager.all_streams_finished():
                logger.info("All video streams have reached the end of file. Exiting application gracefully.")
                break

            # ── Keyboard controls ─────────────────────────────────────────────
            if key in (ord('q'), ord('Q')):
                logger.info("Exit command received (Q).")
                break

            elif key in (ord('+'), ord('=')):
                # Increase playback speed
                current_speed = min(20.0, current_speed + (1.0 if current_speed >= 1.0 else 0.5))
                is_paused = False
                manager.set_speed(current_speed)
                flash_text = f"SPEED: {current_speed:.1f}x"
                flash_expiry = time.time() + 1.5

            elif key in (ord('-'), ord('_')):
                # Decrease playback speed
                current_speed = max(0.5, current_speed - (1.0 if current_speed > 1.0 else 0.25))
                is_paused = False
                manager.set_speed(current_speed)
                flash_text = f"SPEED: {current_speed:.1f}x"
                flash_expiry = time.time() + 1.5

            elif key == ord(' '):
                # Toggle Pause / Play
                is_paused = not is_paused
                manager.set_speed(0.0 if is_paused else current_speed)
                flash_text = "PAUSED" if is_paused else f"RESUMED: {current_speed:.1f}x"
                flash_expiry = time.time() + 1.5

            elif key in (ord('r'), ord('R')):
                # Reset speed to 1.0x
                current_speed = 1.0
                is_paused = False
                manager.set_speed(1.0)
                flash_text = "SPEED RESET: 1.0x (Normal)"
                flash_expiry = time.time() + 1.5

            elif key in (ord('s'), ord('S')):
                # Save a snapshot of the full dashboard grid
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                try:
                    os.makedirs(CAPTURE_DIR, exist_ok=True)
                    filename = f"dashboard_{timestamp}.jpg"
                    filepath = os.path.join(CAPTURE_DIR, filename)
                    # Save the raw dashboard (without the flash text)
                    cv2.imwrite(filepath, dashboard)
                    logger.info(f"Dashboard snapshot saved: {filepath}")
                    flash_text = f"SNAPSHOT SAVED:  {filename}"
                    flash_expiry = time.time() + 2.5
                except Exception as exc:
                    logger.error(f"Failed to save dashboard snapshot: {exc}")
                    flash_text = f"SAVE FAILED: {str(exc)[:40]}"
                    flash_expiry = time.time() + 2.5

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt or window close event captured.")
    except Exception as exc:
        logger.critical(f"Critical error in main loop: {exc}", exc_info=True)
    finally:
        try:
            valid_ts = [ts for ts in last_processed_timestamps.values() if ts is not None]
            shutdown_ts = max(valid_ts) if valid_ts else datetime.datetime.now()
            monitoring_service.shutdown(shutdown_ts)
        except Exception as shutdown_exc:
            logger.error("Failed to cleanly shutdown monitoring service: %s", shutdown_exc)
        manager.stop_all()
        cv2.destroyAllWindows()
        logger.info("Application Closed")
        print("\nShutdown complete. Application closed gracefully.\n")


if __name__ == "__main__":
    main()

