# tests/test_employee_punch_api.py
import unittest
import os
import json
import shutil
import tempfile
import asyncio
import config.settings as settings
from web_dashboard.app import get_employee_attendance_punches


class TestEmployeePunchApi(unittest.TestCase):
    """Test the employee punch cards API endpoint."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self._orig_output_dir = settings.OUTPUT_DIR
        settings.OUTPUT_DIR = self.temp_dir

        # Create known attendance output directory
        known_dir = os.path.join(self.temp_dir, "known")
        os.makedirs(known_dir, exist_ok=True)

        # Create dummy attendance records matching user punch format
        rec1 = {
            "session_id": "SESS_EMP001_20260818_095800",
            "employee_id": "EMP001",
            "employee_name": "Arun Prakash",
            "presence_status": "OUT",
            "entrance_camera_id": "CAM001",
            "exit_camera_id": "CAM001",
            "entry_time": "2026-08-18T09:58:00",
            "exit_time": "2026-08-18T11:37:00",
            "working_duration_seconds": 5940.0,
            "phone_use_duration_seconds": 0.0,
            "phone_use_count": 0,
            "productivity_score": 100.0
        }
        rec2 = {
            "session_id": "SESS_EMP001_20260818_115600",
            "employee_id": "EMP001",
            "employee_name": "Arun Prakash",
            "presence_status": "OUT",
            "entrance_camera_id": "CAM001",
            "exit_camera_id": "CAM001",
            "entry_time": "2026-08-18T11:56:00",
            "exit_time": "2026-08-18T14:08:00",
            "working_duration_seconds": 7920.0,
            "phone_use_duration_seconds": 0.0,
            "phone_use_count": 0,
            "productivity_score": 100.0
        }

        with open(os.path.join(known_dir, "attendance_EMP001_2026-08-18_095800.json"), "w") as f:
            json.dump(rec1, f)
        with open(os.path.join(known_dir, "attendance_EMP001_2026-08-18_115600.json"), "w") as f:
            json.dump(rec2, f)

    def tearDown(self):
        settings.OUTPUT_DIR = self._orig_output_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_employee_punches(self):
        """Verify that punch pairs are formatted with exact time, location, and shift details."""
        data = asyncio.run(get_employee_attendance_punches("EMP001", date="2026-08-18"))

        self.assertEqual(data["employee_id"], "EMP001")
        self.assertEqual(data["employee_name"], "Arun Prakash")
        self.assertEqual(data["punch_count"], 2)

        p1 = data["punches"][0]
        self.assertEqual(p1["in_time"], "09:58 AM")
        self.assertEqual(p1["in_location"], "KANCHIPURAM")
        self.assertEqual(p1["out_time"], "11:37 AM")
        self.assertEqual(p1["out_location"], "KANCHIPURAM")
        self.assertEqual(p1["status"], "COMPLETED")

        p2 = data["punches"][1]
        self.assertEqual(p2["in_time"], "11:56 AM")
        self.assertEqual(p2["out_time"], "02:08 PM")


if __name__ == "__main__":
    unittest.main()
