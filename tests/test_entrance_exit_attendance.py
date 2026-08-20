# tests/test_entrance_exit_attendance.py
import unittest
import os
import shutil
import tempfile
from datetime import datetime, timedelta
import config.settings as settings
from session.attendance_manager import AttendanceManager
from session.global_session_manager import GlobalSessionManager


class TestEntranceExitAttendance(unittest.TestCase):
    """Test entrance-gated IN/OUT attendance lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self._orig_output_dir = settings.OUTPUT_DIR
        settings.OUTPUT_DIR = self.temp_dir
        self.base_time = datetime(2026, 8, 19, 9, 0, 0)
        self.manager = AttendanceManager(lost_timeout_seconds=5)

    def tearDown(self):
        settings.OUTPUT_DIR = self._orig_output_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_entrance_camera_clock_in(self):
        """Entering via CAM001 (Entrance) creates session with presence_status='IN'."""
        session = self.manager.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=10,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=95.0
        )
        self.assertEqual(session.presence_status, "IN")
        self.assertEqual(session.status, "tracking")
        self.assertEqual(session.last_camera_id, "CAM001")
        self.assertEqual(session.last_camera_type, "entrance")
        self.assertEqual(session.entrance_camera_id, "CAM001")

    def test_inner_camera_movement_preserves_in_state(self):
        """Moving to CAM003 (Inner camera) preserves presence_status='IN'."""
        session = self.manager.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=10,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=95.0
        )
        # Move to inner camera (CAM003) at 9:00:10
        t_move = self.base_time + timedelta(seconds=10)
        self.manager.handle_lost_track("CAM001", 10, t_move)
        self.manager.bind_camera_track(session, "CAM003", 25, [150, 150, 250, 250], t_move)

        self.assertEqual(session.presence_status, "IN")
        self.assertEqual(session.status, "tracking")
        self.assertEqual(session.last_camera_id, "CAM003")
        self.assertEqual(session.last_camera_type, "inner")

    def test_inner_camera_timeout_does_not_clock_out(self):
        """Disappearing on an inner camera (CAM003) does NOT trigger exit/clock-out."""
        session = self.manager.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=10,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=95.0
        )
        # Person moves to CAM003 (Inner)
        t_inner = self.base_time + timedelta(seconds=10)
        self.manager.handle_lost_track("CAM001", 10, t_inner)
        self.manager.bind_camera_track(session, "CAM003", 25, [150, 150, 250, 250], t_inner)

        # Person disappears from CAM003 (e.g. sits at desk / out of view)
        t_lost = t_inner + timedelta(seconds=5)
        self.manager.handle_lost_track("CAM003", 25, t_lost)

        # 30 seconds pass (longer than lost_timeout=5s)
        t_timeout = t_lost + timedelta(seconds=30)
        exited = self.manager.process_timeouts(t_timeout)

        # Should NOT be in exited list because last camera was CAM003 (Inner)
        self.assertEqual(len(exited), 0)
        self.assertEqual(session.presence_status, "IN")
        self.assertEqual(session.status, "inside_idle")

        # Active sessions still returns this employee as present inside
        active = self.manager.get_active_sessions()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].employee_id, "EMP001")

    def test_entrance_camera_exit_clocks_out(self):
        """Returning to CAM001 (Entrance) and leaving triggers exit/clock-out."""
        session = self.manager.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=10,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=95.0
        )
        # Move to CAM003 (Inner)
        t_inner = self.base_time + timedelta(seconds=10)
        self.manager.handle_lost_track("CAM001", 10, t_inner)
        self.manager.bind_camera_track(session, "CAM003", 25, [150, 150, 250, 250], t_inner)

        # Walk back to CAM001 (Entrance) to leave
        t_exit_cam = t_inner + timedelta(seconds=60)
        self.manager.handle_lost_track("CAM003", 25, t_exit_cam)
        self.manager.bind_camera_track(session, "CAM001", 30, [110, 110, 210, 210], t_exit_cam)

        # Leave CAM001
        t_leave = t_exit_cam + timedelta(seconds=5)
        self.manager.handle_lost_track("CAM001", 30, t_leave)

        # Timeout occurs on CAM001 (Entrance)
        t_timeout = t_leave + timedelta(seconds=10)
        exited = self.manager.process_timeouts(t_timeout)

        self.assertEqual(len(exited), 1)
        self.assertEqual(exited[0].employee_id, "EMP001")
        self.assertEqual(session.presence_status, "OUT")
        self.assertEqual(session.status, "exited")
        self.assertEqual(session.exit_camera_id, "CAM001")

        # Check JSON record was written
        known_files = os.listdir(os.path.join(self.temp_dir, "known"))
        self.assertTrue(any(f.startswith("attendance_EMP001_") for f in known_files))


if __name__ == "__main__":
    unittest.main()
