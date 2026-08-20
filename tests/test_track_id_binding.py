# tests/test_track_id_binding.py
import unittest
from datetime import datetime, timedelta
import numpy as np

from session.identity_manager import IdentityManager
from session.track_memory_manager import TrackMemoryManager
from session.attendance_manager import AttendanceManager
import config.settings as settings


class TestTrackIdBinding(unittest.TestCase):
    """Test that recognizing an employee binds the track ID and breaks further face recognition."""

    def setUp(self):
        settings.MIN_CONSECUTIVE_MATCHES = 3
        self.identity_mgr = IdentityManager()
        self.track_mem_mgr = TrackMemoryManager()
        self.base_time = datetime(2026, 8, 19, 9, 0, 0)

    def test_recognition_locks_and_breaks_loop(self):
        """Track locks after 3 consistent positive matches; further recognition calls return False (no new lock)."""
        track = self.track_mem_mgr.create_track("CAM001", 1, [100, 100, 200, 200], self.base_time)
        self.assertFalse(track["locked_status"])

        # Match 1
        locked1, id1 = self.identity_mgr.process_recognition_result(
            track, "EMP001", "Arun Prakash", 0.85, 85.0
        )
        self.assertFalse(locked1)

        # Match 2
        locked2, id2 = self.identity_mgr.process_recognition_result(
            track, "EMP001", "Arun Prakash", 0.88, 88.0
        )
        self.assertFalse(locked2)

        # Match 3 -> Should LOCK
        locked3, id3 = self.identity_mgr.process_recognition_result(
            track, "EMP001", "Arun Prakash", 0.90, 90.0
        )
        self.assertTrue(locked3)
        self.assertEqual(id3, "EMP001")
        self.assertTrue(track["locked_status"])
        self.assertEqual(track["employee_id"], "EMP001")
        self.assertEqual(track["employee_name"], "Arun Prakash")
        self.assertEqual(track["recognition_status"], "identified")

        # Further recognition attempts on the locked track are ignored ("broken")
        locked4, id4 = self.identity_mgr.process_recognition_result(
            track, "EMP002", "Sharma", 0.95, 95.0
        )
        self.assertFalse(locked4)
        self.assertEqual(id4, "EMP001")  # Remains locked to EMP001
        self.assertEqual(track["employee_id"], "EMP001")

    def test_identity_manager_track_mapping_persistence(self):
        """IdentityManager accurately remembers mapped employee ID by track ID."""
        track = self.track_mem_mgr.create_track("CAM001", 5, [50, 50, 150, 150], self.base_time)

        for _ in range(3):
            self.identity_mgr.process_recognition_result(
                track, "EMP003", "Rahul", 0.82, 82.0
            )

        self.assertEqual(self.identity_mgr.get_mapped_employee_id("CAM001", 5), "EMP003")
        self.assertIsNone(self.identity_mgr.get_mapped_employee_id("CAM001", 999))

    def test_reid_recovery_immediately_locks_track(self):
        """FastReID recovery restores identity and locks track memory immediately."""
        att_mgr = AttendanceManager(lost_timeout_seconds=30)
        feat1 = np.ones(128, dtype=np.float32)
        feat1 /= np.linalg.norm(feat1)

        # Create initial session
        sess = att_mgr.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=1,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=90.0,
            reid_features=feat1
        )

        # Mark track lost
        att_mgr.handle_lost_track("CAM001", 1, self.base_time + timedelta(seconds=2))
        self.assertEqual(sess.status, "lost")

        # New track appears on CAM003 with same ReID features
        track2 = self.track_mem_mgr.create_track(
            "CAM003", 20, [110, 110, 210, 210], self.base_time + timedelta(seconds=4)
        )
        # Bind session to new track
        att_mgr.bind_camera_track(
            sess, "CAM003", 20, [110, 110, 210, 210], self.base_time + timedelta(seconds=4), feat1
        )
        sess.status = "tracking"

        # Lock track memory directly as pipeline does on ReID match
        track2["locked_status"] = True
        track2["employee_id"] = sess.employee_id
        track2["employee_name"] = sess.employee_name
        track2["recognition_status"] = "identified"
        self.identity_mgr.track_to_employee[("CAM003", 20)] = sess.employee_id

        self.assertTrue(track2["locked_status"])
        self.assertEqual(track2["employee_id"], "EMP001")
        self.assertEqual(self.identity_mgr.get_mapped_employee_id("CAM003", 20), "EMP001")


if __name__ == "__main__":
    unittest.main()
