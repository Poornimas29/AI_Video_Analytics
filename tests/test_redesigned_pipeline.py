# tests/test_redesigned_pipeline.py
"""Unit tests for the redesigned face recognition, identity, tracking, and attendance managers."""

import unittest
from datetime import datetime, timedelta
import numpy as np
from types import SimpleNamespace

from employee_management.embedding_manager import EmbeddingManager
from session.identity_manager import IdentityManager
from session.track_memory_manager import TrackMemoryManager
from session.attendance_manager import AttendanceManager

class TestRedesignedPipeline(unittest.TestCase):
    
    def setUp(self) -> None:
        import os, glob
        from session.attendance_manager import known_dir
        self.base_time = datetime(2026, 7, 16, 12, 0, 0)
        for pattern in ["attendance_EMP001_*.json", "attendance_EMP002_*.json"]:
            for f in glob.glob(os.path.join(known_dir(), pattern)):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_embedding_manager_multi_matching(self) -> None:
        mgr = EmbeddingManager(project_root=".")
        # Setup embeddings: EMP001 has two distinct embeddings (e.g. front and side)
        emb_front = np.zeros(512, dtype=np.float32)
        emb_front[0] = 1.0
        emb_side = np.zeros(512, dtype=np.float32)
        emb_side[1] = 1.0
        
        mgr.employee_embeddings = {
            "EMP001": {
                "name": "Arun",
                "embeddings": [emb_front, emb_side],
                "image_count": 2,
                "images_metadata": []
            }
        }
        mgr._build_gallery_matrix()
        
        # Test query matching front
        query_front = np.zeros(512, dtype=np.float32)
        query_front[0] = 0.9
        query_front[1] = 0.1
        query_front = query_front / np.linalg.norm(query_front)
        emp_id, name, sim = mgr.match_embedding(query_front)
        self.assertEqual(emp_id, "EMP001")
        self.assertGreater(sim, 0.8)

        # Test query matching side
        query_side = np.zeros(512, dtype=np.float32)
        query_side[0] = 0.1
        query_side[1] = 0.9
        query_side = query_side / np.linalg.norm(query_side)
        emp_id, name, sim = mgr.match_embedding(query_side)
        self.assertEqual(emp_id, "EMP001")
        self.assertGreater(sim, 0.8)

    def test_identity_manager_consecutive_locking(self) -> None:
        id_mgr = IdentityManager()
        from collections import deque
        track_mem = {
            "camera_id": "CAM001",
            "track_id": 15,
            "locked_status": False,
            "recognition_count": 0,
            "embedding_history": [],
            "last_matched_employee_id": None,
            "consecutive_count": 0,
            "vote_buffer": deque(maxlen=5),
            "employee_id": None,
            "employee_name": "Unknown",
            "recognition_status": "unknown"
        }
        import config.settings as settings
        settings.MIN_CONSECUTIVE_MATCHES = 2

        # First match: not locked yet
        newly_locked, locked_id = id_mgr.process_recognition_result(
            track_mem, "EMP001", "Arun", 0.90, 90.0
        )
        self.assertFalse(newly_locked)

        # Second match: locked! (MIN_CONSECUTIVE_MATCHES=2)
        newly_locked, locked_id = id_mgr.process_recognition_result(
            track_mem, "EMP001", "Arun", 0.91, 91.0
        )
        self.assertTrue(newly_locked)
        self.assertEqual(locked_id, "EMP001")
        self.assertTrue(track_mem["locked_status"])
        self.assertEqual(track_mem["employee_id"], "EMP001")
        self.assertEqual(track_mem["recognition_status"], "identified")

        # Subsequent matching tries to match someone else: should be ignored because track is locked
        newly_locked, locked_id = id_mgr.process_recognition_result(
            track_mem, "EMP002", "Sharma", 0.95, 95.0
        )
        self.assertFalse(newly_locked)
        self.assertEqual(track_mem["employee_id"], "EMP001") # Still EMP001

    def test_track_memory_timeouts(self) -> None:
        mem_mgr = TrackMemoryManager()
        track = mem_mgr.create_track("CAM001", 15, [100, 100, 200, 200], self.base_time)
        
        self.assertEqual(track["track_status"], "tracking")

        # Mark lost
        mem_mgr.mark_lost("CAM001", 15, self.base_time)
        self.assertEqual(track["track_status"], "lost")

        # Process timeouts prior to timeout limit
        exited = mem_mgr.process_timeouts(self.base_time + timedelta(seconds=2), timeout_seconds=5.0)
        self.assertEqual(len(exited), 0)
        self.assertEqual(track["track_status"], "lost")

        # Process timeouts after timeout limit
        exited = mem_mgr.process_timeouts(self.base_time + timedelta(seconds=6), timeout_seconds=5.0)
        self.assertEqual(len(exited), 1)
        self.assertEqual(track["track_status"], "exited")

    def test_attendance_manager_working_hours(self) -> None:
        att_mgr = AttendanceManager(lost_timeout_seconds=5.0)
        session = att_mgr.create_session(
            employee_id="EMP001",
            employee_name="Arun",
            camera_id="CAM001",
            track_id=15,
            bbox=[100, 100, 200, 200],
            timestamp=self.base_time,
            confidence=90.0
        )

        # Update track seen later
        att_mgr.update_track(session, "CAM001", 15, [105, 105, 205, 205], self.base_time + timedelta(seconds=9), [])
        
        # Mark lost
        att_mgr.handle_lost_track("CAM001", 15, self.base_time + timedelta(seconds=9))
        self.assertEqual(session.status, "lost")

        # Process timeouts after timeout threshold
        exited = att_mgr.process_timeouts(self.base_time + timedelta(seconds=15))
        self.assertEqual(len(exited), 1)
        self.assertEqual(session.status, "exited")
        self.assertEqual(session.working_duration, 9.0)

    def test_unrecognized_track_attendance_records(self) -> None:
        import os
        import glob
        from session.attendance_manager import unknown_dir
        
        # Clean existing Unknown attendance files
        pattern = os.path.join(unknown_dir(), "unknown_track_15_*.json")
        for f in glob.glob(pattern):
            try:
                os.unlink(f)
            except OSError:
                pass

        mem_mgr = TrackMemoryManager()
        track = mem_mgr.create_track("CAM001", 15, [100, 100, 200, 200], self.base_time)
        mem_mgr.mark_lost("CAM001", 15, self.base_time + timedelta(seconds=2))
        
        exited = mem_mgr.process_timeouts(self.base_time + timedelta(seconds=8), timeout_seconds=5.0)
        self.assertEqual(len(exited), 1)
        
        att_mgr = AttendanceManager(lost_timeout_seconds=5.0)
        for t in exited:
            if not t["locked_status"]:
                att_mgr.generate_unrecognized_attendance_record(t)

        # Unrecognized tracks should not create files in unknown_dir
        files = glob.glob(pattern)
        self.assertEqual(len(files), 0)

    def test_reid_feature_fusion_and_matching(self) -> None:
        from employee_management.embedding_manager import EmbeddingManager
        import numpy as np
        
        emb_mgr = EmbeddingManager(project_root=".")
        # Setup dummy gallery embeddings
        dummy_feat = np.ones(1280, dtype=np.float32)
        norm = np.linalg.norm(dummy_feat)
        if norm > 0:
            dummy_feat = dummy_feat / norm
        
        emb_mgr.employee_embeddings["EMP001"] = {
            "employee_id": "EMP001",
            "name": "Arun",
            "embeddings": [],
            "reid_embeddings": [dummy_feat],
            "image_count": 1,
            "images_metadata": []
        }
        emb_mgr._build_gallery_matrix()
        
        # Test exact match
        best_id, best_name, score = emb_mgr.match_reid_embedding(dummy_feat)
        self.assertEqual(best_id, "EMP001")
        self.assertEqual(best_name, "Arun")
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_cross_camera_entry_cam1_recog_cam2_exit_cam3(self) -> None:
        """Verify full lifecycle: Entered on CAM001, Recognized on CAM002, Exited on CAM003."""
        import os
        import json
        import glob
        import os
        from session.attendance_manager import AttendanceManager, known_dir
        target_f = os.path.join(known_dir(), "attendance_EMP001_20260727.json")
        if os.path.exists(target_f):
            os.remove(target_f)

        t0 = datetime(2026, 7, 27, 8, 0, 0)   # 08:00:00 - Enter CAM001
        t1 = datetime(2026, 7, 27, 8, 5, 0)   # 08:05:00 - Recognized on CAM002
        t2 = datetime(2026, 7, 27, 8, 30, 0)  # 08:30:00 - Move to CAM003
        t3 = datetime(2026, 7, 27, 11, 30, 0) # 11:30:00 - Exit on CAM003

        att_mgr = AttendanceManager(lost_timeout_seconds=60)

        # 1. Enter CAM001 (Main Entrance)
        session = att_mgr.create_session(
            employee_id="EMP001",
            employee_name="Arun Prakash",
            camera_id="CAM001",
            track_id=1,
            bbox=[100, 100, 200, 300],
            timestamp=t0,
            confidence=85.0
        )
        self.assertEqual(session.entrance_camera_id, "CAM001")
        self.assertEqual(session.first_seen, t0)

        # 2. Recognized / Move to CAM002 (Kitchen Entrance / Transition)
        att_mgr.bind_camera_track(
            session=session,
            camera_id="CAM002",
            track_id=5,
            bbox=[150, 150, 250, 350],
            timestamp=t1
        )
        self.assertEqual(session.first_seen, t0)  # Preserves CAM001 entry time!
        self.assertEqual(session.entrance_camera_id, "CAM001")

        # 3. Move to CAM003 (Main Inner) and stay until exit
        att_mgr.bind_camera_track(
            session=session,
            camera_id="CAM003",
            track_id=12,
            bbox=[200, 200, 300, 400],
            timestamp=t2
        )
        att_mgr.update_track(
            session=session,
            camera_id="CAM003",
            track_id=12,
            bbox=[210, 210, 310, 410],
            timestamp=t3,
            phone_dets=[]
        )

        # 4. Exit on CAM003
        session.status = "exited"
        session.presence_status = "OUT"
        session.exit_camera_id = "CAM003"
        session.last_seen = t3
        
        att_mgr.generate_attendance_record(session)

        # Verify output JSON
        pattern = os.path.join(known_dir(), "attendance_EMP001_20260727*.json")
        matching = glob.glob(pattern)
        self.assertTrue(len(matching) >= 1)

        with open(matching[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["employee_id"], "EMP001")
        self.assertEqual(data["entrance_camera_id"], "CAM001")
        self.assertEqual(data["first_checkin"], "08:00:00")
        self.assertEqual(data["exit_camera_id"], "CAM003")
        self.assertEqual(data["last_checkout"], "11:30:00")
        self.assertEqual(data["total_shop_duration"], "03h 30m 00s")
        self.assertAlmostEqual(data["working_duration_seconds"], 12600.0, delta=1.0)
        self.assertEqual(data["events"][0]["event_type"], "CHECK_IN")
        self.assertEqual(data["events"][0]["camera_id"], "CAM001")
        self.assertEqual(data["events"][-1]["event_type"], "CHECK_OUT")
        self.assertEqual(data["events"][-1]["camera_id"], "CAM003")

        # Cleanup test file
        for mf in matching:
            os.unlink(mf)

    def test_daily_timer_pauses_and_resumes_on_reentry(self):
        """Test that an employee's working timer resumes from prior visits instead of resetting to 0."""
        import json
        import glob
        import os
        from session.attendance_manager import AttendanceManager, known_dir

        target_f = os.path.join(known_dir(), "attendance_EMP002_20260727.json")
        if os.path.exists(target_f):
            os.remove(target_f)

        att_mgr = AttendanceManager(lost_timeout_seconds=60)
        t_v1_in = datetime(2026, 7, 27, 8, 0, 0)
        t_v1_mid = datetime(2026, 7, 27, 8, 30, 0)
        t_v1_out = datetime(2026, 7, 27, 9, 0, 0)

        # ── Visit 1: 08:00 to 09:00 (1 hour) ──
        s1 = att_mgr.create_session(
            employee_id="EMP002",
            employee_name="Sharma",
            camera_id="CAM001",
            track_id=1,
            bbox=[100, 100, 200, 300],
            timestamp=t_v1_in,
            confidence=90.0
        )
        self.assertEqual(s1.working_duration, 0.0)

        # Update tracking on CAM001
        att_mgr.update_track(s1, "CAM001", 1, [100, 100, 200, 300], t_v1_mid, [])
        att_mgr.update_track(s1, "CAM001", 1, [100, 100, 200, 300], t_v1_out, [])
        s1.status = "exited"
        s1.presence_status = "OUT"
        s1.exit_camera_id = "CAM001"
        s1.last_seen = t_v1_out
        att_mgr.generate_attendance_record(s1)

        # ── Visit 2 (Re-entry after break): 10:00 ──
        t_v2_in = datetime(2026, 7, 27, 10, 0, 0)
        t_v2_out = datetime(2026, 7, 27, 10, 30, 0)
        s2 = att_mgr.create_session(
            employee_id="EMP002",
            employee_name="Sharma",
            camera_id="CAM001",
            track_id=2,
            bbox=[100, 100, 200, 300],
            timestamp=t_v2_in,
            confidence=90.0
        )

        # Verify timer RESUMED from 3600s (1h 00m)
        self.assertAlmostEqual(s2.prior_accumulated_seconds, 3600.0, delta=1.0)
        self.assertAlmostEqual(s2.working_duration, 3600.0, delta=1.0)

        # Update tracking for 30 more minutes
        att_mgr.update_track(s2, "CAM001", 2, [100, 100, 200, 300], t_v2_out, [])
        s2.status = "exited"
        s2.presence_status = "OUT"
        s2.exit_camera_id = "CAM001"
        s2.last_seen = t_v2_out
        att_mgr.generate_attendance_record(s2)

        # Verify cumulative JSON has 2 visits and total duration of 1h 30m
        with open(target_f, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["total_visits_count"], 2)
        self.assertEqual(data["total_shop_duration"], "01h 30m 00s")
        self.assertAlmostEqual(data["working_duration_seconds"], 5400.0, delta=1.0)
        self.assertEqual(len(data["entries_and_exits"]), 2)

        # Cleanup
        if os.path.exists(target_f):
            os.remove(target_f)

if __name__ == "__main__":
    unittest.main()
