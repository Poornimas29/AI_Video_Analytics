# session/attendance_manager.py
"""AttendanceManager manages attendance sessions, working hours, and reports.

Output folder structure
-----------------------
output/
  known/    ← one JSON per identified employee session
  unknown/  ← one JSON per unrecognized person track
  reports/  ← daily/weekly summary reports
"""

import os
import glob
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Any, Tuple
import numpy as np
import config.settings as settings
from session.global_session_manager import GlobalSessionManager, GlobalSession

logger = logging.getLogger(__name__)

# ── Sub-folder helpers ─────────────────────────────────────────────────────────────────
def _subdir(name: str) -> str:
    """Return path of output/<name>/ creating it if necessary."""
    path = os.path.join(settings.OUTPUT_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


def known_dir() -> str:   return _subdir("known")
def unknown_dir() -> str: return _subdir("unknown")
def reports_dir() -> str: return _subdir("reports")


class AttendanceManager(GlobalSessionManager):
    """Manages global employee attendance session states, working durations, and logs."""
    
    def __init__(self, lost_timeout_seconds: float = None) -> None:
        timeout = lost_timeout_seconds if lost_timeout_seconds is not None else settings.TRACK_TIMEOUT
        super().__init__(lost_timeout_seconds=int(timeout))
        self.cleanup_expired_unknown_files()

    def create_session(
        self,
        employee_id: str,
        employee_name: str,
        camera_id: str,
        track_id: int,
        bbox: List[int],
        timestamp: datetime,
        confidence: float,
        reid_features: Optional[np.ndarray] = None,
        reid_hist: Optional[np.ndarray] = None
    ) -> GlobalSession:
        """Creates or reactivates a global session, logging the event on initial Entry."""
        session = super().create_session(
            employee_id=employee_id,
            employee_name=employee_name,
            camera_id=camera_id,
            track_id=track_id,
            bbox=bbox,
            timestamp=timestamp,
            confidence=confidence,
            reid_features=reid_features,
            reid_hist=reid_hist
        )
        
        # Log Attendance Started if it is a new session
        if session.first_seen == timestamp:
            cam_role = settings.get_camera_type(camera_id).title()
            
            # Check for prior completed visits today to resume the cumulative timer
            date_str = timestamp.strftime('%Y%m%d')
            _, _, prior_work_sec, prior_phone_sec, prior_phone_cnt = self.get_daily_attendance_summary(
                employee_id, date_str, timestamp, timestamp
            )
            if prior_work_sec > 0:
                session.prior_accumulated_seconds = prior_work_sec
                session.prior_phone_seconds = prior_phone_sec
                session.prior_phone_count = prior_phone_cnt
                m, s = divmod(int(prior_work_sec), 60)
                h, m = divmod(m, 60)
                prior_str = f"{h}h {m}m {s}s"
                print(f"Daily Timer Resumed from: {prior_str}")
                logger.info(
                    "[Attendance] Re-entry for %s (%s) — Resumed cumulative timer from %s",
                    employee_name, employee_id, prior_str
                )

            print("----------------------")
            print("Attendance Started (IN)")
            print(f"Employee ID: {employee_id}")
            print(f"Employee Name: {employee_name}")
            print(f"Status: IN (Inside)")
            print(f"Camera: {camera_id} ({cam_role})")
            print(f"Entry Time: {timestamp:%Y-%m-%d %H:%M:%S}")
            print("----------------------")
            logger.info(
                "[Attendance] Started (IN) - Employee ID %s (%s) at %s via %s (%s)",
                employee_id, employee_name, timestamp, camera_id, cam_role
            )
            
        return session

    def get_daily_attendance_summary(
        self, employee_id: str, date_str: str, current_first_seen: datetime, current_last_seen: datetime
    ) -> Tuple[datetime, datetime, float, float, int]:
        """Read today's consolidated JSON record in output/known/ to build cumulative totals."""
        first_entry = current_first_seen
        last_exit = current_last_seen
        total_work_seconds = 0.0
        total_phone_seconds = 0.0
        total_phone_count = 0
        try:
            pattern = os.path.join(known_dir(), f"attendance_{employee_id}_{date_str}*.json")
            for filepath in glob.glob(pattern):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    entry_val = datetime.fromisoformat(data["entry_time"])
                    exit_val = datetime.fromisoformat(data["exit_time"])
                    if entry_val < first_entry:
                        first_entry = entry_val
                    if exit_val > last_exit:
                        last_exit = exit_val
                    total_work_seconds = max(total_work_seconds, data.get("working_duration_seconds", 0.0))
                    total_phone_seconds = max(total_phone_seconds, data.get("phone_use_duration_seconds", 0.0))
                    total_phone_count = max(total_phone_count, data.get("phone_use_count", 0))
                except Exception:
                    pass
        except Exception:
            pass
        return first_entry, last_exit, total_work_seconds, total_phone_seconds, total_phone_count

    def process_timeouts(self, timestamp: datetime) -> List[GlobalSession]:
        """Checks for timed out sessions from Entrance cameras and records exit details."""
        self.cleanup_expired_unknown_files()
        exited_sessions = super().process_timeouts(timestamp)
        for session in exited_sessions:
            duration_sec = session.working_duration
            m, s = divmod(int(duration_sec), 60)
            h, m = divmod(m, 60)
            duration_str = f"{h}h {m}m {s}s"
            
            # Save the attendance record into the employee's single daily file
            self.generate_attendance_record(session)
            
            # Calculate today's full summary
            date_str = session.last_seen.strftime('%Y%m%d')
            first_entry, last_exit, total_work_seconds, total_phone_seconds, total_phone_count = self.get_daily_attendance_summary(
                session.employee_id, date_str, session.first_seen, session.last_seen
            )

            # Working time string
            tot_m, tot_s = divmod(int(total_work_seconds), 60)
            tot_h, tot_m = divmod(tot_m, 60)
            total_work_str = f"{tot_h}h {tot_m}m {tot_s}s"
            # Phone usage string
            ph_m, ph_s = divmod(int(total_phone_seconds), 60)
            ph_h, ph_m = divmod(ph_m, 60)
            total_phone_str = f"{ph_h}h {ph_m}m {ph_s}s"
            
            total_duration_str = total_work_str  # keep existing variable for consistency
            
            exit_cam = getattr(session, "exit_camera_id", None) or getattr(session, "last_camera_id", "CAM001")
            print("----------------------")
            print("Attendance Completed (OUT)")
            print(f"Employee ID: {session.employee_id}")
            print(f"Employee Name: {session.employee_name}")
            print(f"Status: OUT (Exited)")
            print(f"Exit Camera: {exit_cam}")
            print(f"First Appearance (Today): {first_entry:%Y-%m-%d %H:%M:%S}")
            print(f"Last Departure (Today): {last_exit:%Y-%m-%d %H:%M:%S}")
            print(f"Working Hours (Session): {duration_str}")
            print(f"Total Working Hours (Today): {total_work_str}")
            print(f"Total Mobile Usage (Today): {total_phone_str} (Count: {total_phone_count})")
            print("----------------------")
            logger.info(
                "[Attendance] Completed (OUT) - Employee ID %s | Exit Cam: %s | First: %s | Last: %s | Cumulative Today: %s",
                session.employee_id, exit_cam, first_entry, last_exit, total_duration_str
            )
            
        return exited_sessions

    def get_employee_total_summary(self, employee_id: str) -> Tuple[float, float, int]:
        """Aggregate totals for one employee across all known/ records."""
        total_work_seconds = 0.0
        total_phone_seconds = 0.0
        total_phone_count = 0
        try:
            pattern = os.path.join(known_dir(), f"attendance_{employee_id}_*.json")
            for filepath in glob.glob(pattern):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    total_work_seconds += data.get("working_duration_seconds", 0.0)
                    total_phone_seconds += data.get("phone_use_duration_seconds", 0.0)
                    total_phone_count += data.get("phone_use_count", 0)
                except Exception:
                    pass
        except Exception:
            pass
        return total_work_seconds / 3600.0, total_phone_seconds, total_phone_count

    def generate_attendance_record(self, session: "GlobalSession") -> None:
        """Write and merge all sessions for an employee into a single consolidated daily JSON file."""
        try:
            is_active = (getattr(session, "presence_status", "OUT") == "IN" and session.status != "exited")
            exit_type = getattr(session, "exit_type", "CHECK_OUT" if not is_active else "IN_PROGRESS")
            if getattr(session, "presence_status", "OUT") == "VIDEO_ENDED":
                exit_type = "VIDEO_ENDED"
            
            # Close unclosed camera history entries with the person's last_seen time for exited sessions
            if not is_active:
                for entry in session.camera_history:
                    if entry.get("exit_time") is None:
                        entry["exit_time"] = session.last_seen

            total_shop_sec = 0.0
            if session.first_seen and session.last_seen:
                total_shop_sec = max(0.0, (session.last_seen - session.first_seen).total_seconds())
            
            m, s = divmod(int(total_shop_sec), 60)
            h, m = divmod(m, 60)
            dur_formatted = f"{h:02d}h {m:02d}m {s:02d}s"

            entrance_cam = getattr(session, "entrance_camera_id", None) or (
                session.camera_history[0]["cam_id"] if session.camera_history else getattr(session, "last_camera_id", "CAM001")
            )
            exit_cam = getattr(session, "exit_camera_id", None) or getattr(session, "last_camera_id", entrance_cam)

            # Build discrete events list for this session
            events = [
                {
                    "event_type": "CHECK_IN",
                    "camera_id": entrance_cam,
                    "timestamp": session.first_seen.isoformat(),
                    "time": session.first_seen.strftime("%H:%M:%S")
                }
            ]
            for e in session.camera_history:
                if e.get("exit_time"):
                    events.append({
                        "event_type": "CAMERA_TRANSITION",
                        "camera_id": e["cam_id"],
                        "entry_time": e["entry_time"].isoformat() if hasattr(e["entry_time"], "isoformat") else str(e["entry_time"]),
                        "exit_time": e["exit_time"].isoformat() if hasattr(e["exit_time"], "isoformat") else str(e["exit_time"])
                    })
            if not is_active:
                if exit_type == "VIDEO_ENDED":
                    events.append({
                        "event_type": "VIDEO_ENDED",
                        "camera_id": exit_cam,
                        "timestamp": session.last_seen.isoformat(),
                        "time": session.last_seen.strftime("%H:%M:%S")
                    })
                else:
                    events.append({
                        "event_type": "CHECK_OUT",
                        "camera_id": exit_cam,
                        "timestamp": session.last_seen.isoformat(),
                        "time": session.last_seen.strftime("%H:%M:%S")
                    })

            cam_history_out = [
                {
                    "camera_id": e["cam_id"],
                    "entry_time": e["entry_time"].isoformat() if hasattr(e["entry_time"], "isoformat") else str(e["entry_time"]),
                    "exit_time": e["exit_time"].isoformat() if e.get("exit_time") and hasattr(e["exit_time"], "isoformat") else (str(e["exit_time"]) if e.get("exit_time") else None),
                }
                for e in session.camera_history
            ]

            last_checkout_str = "Video Ended" if exit_type == "VIDEO_ENDED" else (session.last_seen.strftime("%H:%M:%S") if not is_active else "")
            session_obj = {
                "session_id": session.session_id,
                "first_checkin": session.first_seen.strftime("%H:%M:%S"),
                "last_checkout": last_checkout_str,
                "entrance_camera_id": entrance_cam,
                "exit_camera_id": exit_cam if not is_active else "",
                "entry_time": session.first_seen.isoformat(),
                "exit_time": session.last_seen.isoformat() if not is_active else None,
                "exit_type": exit_type,
                "duration": dur_formatted if not is_active else "In Progress",
                "duration_seconds": round(total_shop_sec, 2),
                "phone_use_duration_seconds": round(session.phone_use_duration, 2),
                "phone_use_count": len(session.phone_use_history),
                "productivity_score": round(session.productivity_score, 2),
                "recognition_confidence": round(session.recognition_confidence, 2),
                "events": events,
                "camera_history": cam_history_out,
            }

            date_str = session.first_seen.strftime('%Y%m%d')
            filename = f"attendance_{session.employee_id}_{date_str}.json"
            filepath = os.path.join(known_dir(), filename)

            # Load existing record if present to merge multiple sessions into this single file
            existing_sessions = []
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        old_doc = json.load(f)
                    existing_sessions = old_doc.get("sessions", [])
                except Exception:
                    existing_sessions = []

            # Update or append this session
            session_found = False
            for idx, s_item in enumerate(existing_sessions):
                if s_item.get("session_id") == session.session_id:
                    existing_sessions[idx] = session_obj
                    session_found = True
                    break
            if not session_found:
                existing_sessions.append(session_obj)

            # Merge overlapping time intervals across cameras to compute true physical shop visits
            intervals = []
            for s_item in existing_sessions:
                try:
                    e_t = datetime.fromisoformat(s_item["entry_time"])
                    x_raw = s_item.get("exit_time")
                    x_t = datetime.fromisoformat(x_raw) if x_raw else None
                    if x_t and e_t <= x_t:
                        intervals.append((e_t, x_t, s_item))
                    elif not x_t:
                        # Active session without exit time yet
                        intervals.append((e_t, None, s_item))
                except Exception:
                    pass

            intervals.sort(key=lambda it: it[0])
            merged_visits = []
            if intervals:
                curr_start, curr_end, curr_s = intervals[0]
                curr_ent_cam = curr_s.get("entrance_camera_id", "CAM001")
                curr_exit_cam = curr_s.get("exit_camera_id", "CAM001")
                curr_exit_type = curr_s.get("exit_type", "CHECK_OUT")
                
                for next_start, next_end, next_s in intervals[1:]:
                    # If overlapping or contiguous (within 60s gap), merge into same visit
                    if curr_end is not None and next_start <= curr_end + timedelta(seconds=60):
                        if next_end is None or next_end > curr_end:
                            curr_end = next_end
                            curr_exit_cam = next_s.get("exit_camera_id", curr_exit_cam)
                            curr_exit_type = next_s.get("exit_type", curr_exit_type)
                    else:
                        dur_sec = max(0.0, (curr_end - curr_start).total_seconds()) if curr_end else 0.0
                        m, s = divmod(int(dur_sec), 60)
                        h, m = divmod(m, 60)
                        v_exit_str = "Video Ended" if curr_exit_type == "VIDEO_ENDED" else (curr_end.strftime("%H:%M:%S") if curr_end else "--:--")
                        v_status_str = "VIDEO_ENDED" if curr_exit_type == "VIDEO_ENDED" else ("COMPLETED" if curr_end else "ACTIVE")
                        merged_visits.append({
                            "entrance_camera": curr_ent_cam,
                            "entry_time": curr_start.strftime("%H:%M:%S"),
                            "exit_camera": curr_exit_cam if curr_end else "",
                            "exit_time": v_exit_str,
                            "exit_type": curr_exit_type,
                            "status": v_status_str,
                            "duration": f"{h:02d}h {m:02d}m {s:02d}s" if curr_end else "In Progress",
                            "duration_seconds": round(dur_sec, 2),
                        })
                        curr_start, curr_end = next_start, next_end
                        curr_ent_cam = next_s.get("entrance_camera_id", "CAM001")
                        curr_exit_cam = next_s.get("exit_camera_id", "CAM001")
                        curr_exit_type = next_s.get("exit_type", "CHECK_OUT")

                dur_sec = max(0.0, (curr_end - curr_start).total_seconds()) if curr_end else 0.0
                m, s = divmod(int(dur_sec), 60)
                h, m = divmod(m, 60)
                v_exit_str = "Video Ended" if curr_exit_type == "VIDEO_ENDED" else (curr_end.strftime("%H:%M:%S") if curr_end else "--:--")
                v_status_str = "VIDEO_ENDED" if curr_exit_type == "VIDEO_ENDED" else ("COMPLETED" if curr_end else "ACTIVE")
                merged_visits.append({
                    "entrance_camera": curr_ent_cam,
                    "entry_time": curr_start.strftime("%H:%M:%S"),
                    "exit_camera": curr_exit_cam if curr_end else "",
                    "exit_time": v_exit_str,
                    "exit_type": curr_exit_type,
                    "status": v_status_str,
                    "duration": f"{h:02d}h {m:02d}m {s:02d}s" if curr_end else "In Progress",
                    "duration_seconds": round(dur_sec, 2),
                })

            all_entries = [datetime.fromisoformat(s["entry_time"]) for s in existing_sessions if s.get("entry_time")]
            all_exits = [datetime.fromisoformat(s["exit_time"]) for s in existing_sessions if s.get("exit_time")]
            first_dt = min(all_entries) if all_entries else session.first_seen
            last_dt = max(all_exits) if all_exits else (session.last_seen if not is_active else None)

            cum_work_sec = sum(v["duration_seconds"] for v in merged_visits) if merged_visits else total_shop_sec
            cum_phone_sec = sum(s.get("phone_use_duration_seconds", 0.0) for s in existing_sessions)
            cum_phone_cnt = sum(s.get("phone_use_count", 0) for s in existing_sessions)

            tot_m, tot_s = divmod(int(cum_work_sec), 60)
            tot_h, tot_m = divmod(tot_m, 60)
            cum_dur_str = f"{tot_h:02d}h {tot_m:02d}m {tot_s:02d}s"

            # Build discrete numbered visits from merged real-world intervals
            entries_and_exits = []
            for idx, v in enumerate(merged_visits, start=1):
                entries_and_exits.append({
                    "visit_number": idx,
                    "entrance_camera": v["entrance_camera"],
                    "entry_time": v["entry_time"],
                    "exit_camera": v["exit_camera"],
                    "exit_time": v["exit_time"],
                    "exit_type": v.get("exit_type", "CHECK_OUT"),
                    "status": v.get("status", "COMPLETED"),
                    "duration": v["duration"],
                    "duration_seconds": v["duration_seconds"],
                })

            # Flatten all events across sessions in chronological order
            all_events = []
            for s_item in existing_sessions:
                all_events.extend(s_item.get("events", []))

            def _event_sort_key(ev):
                ts = ev.get("timestamp") or ev.get("entry_time") or ""
                # Priority: CHECK_IN (0), CAMERA_TRANSITION (1), CHECK_OUT (2)
                t_order = 0 if ev.get("event_type") == "CHECK_IN" else (2 if ev.get("event_type") == "CHECK_OUT" else 1)
                return (str(ts), t_order)

            try:
                all_events.sort(key=_event_sort_key)
            except Exception:
                pass

            record = {
                "employee_id": session.employee_id,
                "employee_name": session.employee_name,
                "date": session.first_seen.strftime("%Y-%m-%d"),
                "presence_status": getattr(session, "presence_status", "OUT"),
                "first_checkin": first_dt.strftime("%H:%M:%S"),
                "last_checkout": last_dt.strftime("%H:%M:%S") if last_dt else "",
                "entrance_camera_id": entries_and_exits[0]["entrance_camera"] if entries_and_exits else entrance_cam,
                "exit_camera_id": entries_and_exits[-1]["exit_camera"] if entries_and_exits and entries_and_exits[-1]["exit_camera"] else exit_cam,
                "entry_time": first_dt.isoformat(),
                "exit_time": last_dt.isoformat() if last_dt else None,
                "total_shop_duration": cum_dur_str,
                "working_hours": round(cum_work_sec / 3600.0, 4),
                "working_duration_seconds": round(cum_work_sec, 2),
                "phone_use_duration_seconds": round(cum_phone_sec, 2),
                "phone_use_count": cum_phone_cnt,
                "productivity_score": round(session.productivity_score, 2),
                "recognition_confidence": round(session.recognition_confidence, 2),
                "total_visits_count": len(entries_and_exits),
                "entries_and_exits": entries_and_exits,
                "events": all_events,
                "sessions": existing_sessions,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            logger.info("[AttendanceManager] Known record written/merged → %s", filepath)
        except Exception as exc:
            logger.error("[AttendanceManager] Failed to write known record: %s", exc)

    def generate_unrecognized_attendance_record(self, track: dict) -> None:
        """No-op: Unrecognized / unknown tracks are not recorded to disk."""
        return

    def cleanup_expired_unknown_files(self) -> None:
        """Scan output/unknown/ and purge all files."""
        try:
            patterns = [
                os.path.join(unknown_dir(), "unknown_track_*.json"),
                os.path.join(unknown_dir(), "attendance_Unknown_*.json"),
            ]
            for pattern in patterns:
                for filepath in glob.glob(pattern):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
        except Exception:
            pass


    def generate_daily_summary_report(self, date_str: str) -> None:
        """Generate the End-of-Day (EOD) report for known registered employees.

        Saved files
        -----------
        output/reports/daily_report_<YYYYMMDD>.json   — combined master report
        output/reports/report_known_<YYYYMMDD>.json   — known employees only
        """
        import glob

        STREAM_GAP_THRESHOLD = 5 * 60  # 5 minutes = stream interruption

        # ── Known employees ───────────────────────────────────────────────────
        known_sessions: dict = {}
        pattern_known = os.path.join(known_dir(), f"attendance_EMP*_{date_str}*.json")
        for filepath in glob.glob(pattern_known):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                emp_id = data.get("employee_id")
                if not emp_id or emp_id == "Unknown" or emp_id is None:
                    continue
                emp_name = data.get("employee_name", "Unknown")
                if emp_id not in known_sessions:
                    known_sessions[emp_id] = {"name": emp_name, "records": []}

                session_list = data.get("sessions") or [data]
                for s_item in session_list:
                    entry = datetime.fromisoformat(s_item["entry_time"])
                    exit_t = datetime.fromisoformat(s_item["exit_time"])
                    work_sec = s_item.get("duration_seconds") or s_item.get("working_duration_seconds", 0.0)
                    phone_sec = s_item.get("phone_use_duration_seconds", 0.0)
                    phone_cnt = s_item.get("phone_use_count", 0)
                    known_sessions[emp_id]["records"].append({
                        "entry": entry, "exit": exit_t,
                        "work_sec": work_sec, "phone_sec": phone_sec, "phone_cnt": phone_cnt,
                    })
            except Exception as exc:
                logger.error("[AttendanceManager] Failed to process %s: %s", filepath, exc)

        known_report = []
        total_stream_interruptions = 0
        grand_total_work = 0.0
        grand_total_phone = 0.0

        for emp_id, info in known_sessions.items():
            records = sorted(info["records"], key=lambda r: r["entry"])
            total_work = 0.0
            total_phone = 0.0
            total_phone_cnt = 0
            stops = []
            stop_num = 1
            seg_work = 0.0
            seg_phone = 0.0
            seg_entry = None
            seg_exit = None
            prev_exit = None
            interruptions = 0

            for rec in records:
                if prev_exit is not None and (rec["entry"] - prev_exit).total_seconds() > STREAM_GAP_THRESHOLD:
                    stops.append({
                        "stop_number": stop_num,
                        "entry_time": seg_entry.strftime("%H:%M:%S") if seg_entry else "",
                        "exit_time": seg_exit.strftime("%H:%M:%S") if seg_exit else "",
                        "working_hours": round(seg_work / 3600.0, 4),
                        "mobile_usage_hours": round(seg_phone / 3600.0, 4),
                    })
                    stop_num += 1
                    interruptions += 1
                    seg_work = 0.0
                    seg_phone = 0.0
                    seg_entry = rec["entry"]

                if seg_entry is None:
                    seg_entry = rec["entry"]

                seg_work += rec["work_sec"]
                seg_phone += rec["phone_sec"]
                seg_exit = rec["exit"]
                total_work += rec["work_sec"]
                total_phone += rec["phone_sec"]
                total_phone_cnt += rec["phone_cnt"]
                prev_exit = rec["exit"]

            if seg_work > 0 or seg_phone > 0 or seg_entry is not None:
                stops.append({
                    "stop_number": stop_num,
                    "entry_time": seg_entry.strftime("%H:%M:%S") if seg_entry else "",
                    "exit_time": seg_exit.strftime("%H:%M:%S") if seg_exit else "",
                    "working_hours": round(seg_work / 3600.0, 4),
                    "mobile_usage_hours": round(seg_phone / 3600.0, 4),
                })

            total_stream_interruptions += interruptions
            grand_total_work += total_work
            grand_total_phone += total_phone

            known_report.append({
                "employee_id": emp_id,
                "employee_name": info["name"],
                "total_working_hours": round(total_work / 3600.0, 4),
                "total_mobile_usage_hours": round(total_phone / 3600.0, 4),
                "total_phone_use_count": total_phone_cnt,
                "stream_stops": interruptions,
                "stops": stops,
            })

        # ── Overall summary ───────────────────────────────────────────────────
        overall_summary = {
            "total_known_employees": len(known_report),
            "total_stream_interruptions": total_stream_interruptions,
            "total_working_hours_all_employees": round(grand_total_work / 3600.0, 4),
            "total_mobile_usage_hours_all_employees": round(grand_total_phone / 3600.0, 4),
        }

        # ── Write reports ─────────────────────────────────────────────────────
        final_report = {
            "date": date_str,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "known_employees": known_report,
            "overall_summary": overall_summary,
        }

        master_path = os.path.join(reports_dir(), f"daily_report_{date_str}.json")
        known_path = os.path.join(reports_dir(), f"report_known_{date_str}.json")

        def _write(path, data):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                logger.info("[AttendanceManager] Report written → %s", path)
            except Exception as exc:
                logger.error("[AttendanceManager] Failed to write %s: %s", path, exc)

        _write(master_path, final_report)
        _write(known_path, {"date": date_str, "known_employees": known_report, "overall_summary": overall_summary})

    def generate_markdown_timeline_table(self, date_str: str = None) -> str:
        """Generate the clean markdown attendance table with daily entry/exit breakdowns.

        Format:
        | Employee                | Entry | Exit  |   Duration |
        | ----------------------- | ----- | ----- | ---------: |
        | Employee 001            | 09:05 | 12:30 |     3h 25m |
        | Employee 001            | 13:15 | 17:45 |     4h 30m |
        | **Total Working Hours** |       |       | **7h 55m** |
        """
        import glob
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")

        pattern = os.path.join(known_dir(), f"attendance_EMP*_{date_str}_*.json")
        sessions_by_emp: dict = {}
        for filepath in glob.glob(pattern):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                emp_id = data.get("employee_id")
                emp_name = data.get("employee_name", emp_id)
                entry_dt = datetime.fromisoformat(data["entry_time"])
                exit_dt = datetime.fromisoformat(data["exit_time"])
                dur_sec = data.get("working_duration_seconds", 0.0)

                key = (emp_id, emp_name)
                if key not in sessions_by_emp:
                    sessions_by_emp[key] = []
                sessions_by_emp[key].append({
                    "entry": entry_dt,
                    "exit": exit_dt,
                    "duration_sec": dur_sec
                })
            except Exception:
                pass

        def _format_dur(secs: float) -> str:
            m, s = divmod(int(secs), 60)
            h, m = divmod(m, 60)
            return f"{h}h {m:02d}m"

        if not sessions_by_emp:
            # Check if there are any records across all dates to show an example
            all_known = glob.glob(os.path.join(known_dir(), "attendance_EMP*.json"))
            if not all_known:
                return (
                    "| Employee                | Entry | Exit  |   Duration |\n"
                    "| ----------------------- | ----- | ----- | ---------: |\n"
                    "| *No active records yet* | --:-- | --:-- |      0h 00m |\n"
                    "| **Total Working Hours** |       |       | **0h 00m** |"
                )

        lines = [
            "| Employee                | Entry | Exit  |   Duration |",
            "| ----------------------- | ----- | ----- | ---------: |"
        ]

        grand_total_secs = 0.0
        num_employees = len(sessions_by_emp)

        for (emp_id, emp_name), records in sessions_by_emp.items():
            records.sort(key=lambda r: r["entry"])
            emp_total_secs = 0.0
            display_name = f"{emp_name}" if emp_name else f"Employee {emp_id}"
            
            for r in records:
                entry_str = r["entry"].strftime("%H:%M")
                exit_str = r["exit"].strftime("%H:%M")
                dur_str = _format_dur(r["duration_sec"])
                emp_total_secs += r["duration_sec"]
                lines.append(f"| {display_name:<23} | {entry_str:<5} | {exit_str:<5} | {dur_str:>10} |")

            grand_total_secs += emp_total_secs
            if num_employees > 1:
                emp_tot_str = _format_dur(emp_total_secs)
                lines.append(f"| **Subtotal ({emp_id})**    |       |       | **{emp_tot_str:>8}** |")

        grand_tot_str = _format_dur(grand_total_secs)
        lines.append(f"| **Total Working Hours** |       |       | **{grand_tot_str:>8}** |")

        md_table = "\n".join(lines)
        report_path = os.path.join(reports_dir(), f"timeline_table_{date_str}.md")
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(md_table + "\n")
            logger.info("[AttendanceManager] Timeline report written → %s", report_path)
        except Exception:
            pass
        return md_table


