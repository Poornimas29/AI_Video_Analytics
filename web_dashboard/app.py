# web_dashboard/app.py
"""FastAPI Backend Server for CCTV AI Employee Monitoring & Attendance Dashboard.

Provides:
- REST API for live attendance, daily timelines, KPI stats, and employee directory.
- MJPEG live video streaming for multi-camera grid and individual cameras.
- Direct CSV export for daily attendance records.
"""

from __future__ import annotations

import os
import glob
import json
import time
import cv2
import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from fastapi import FastAPI, Response, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import config.settings as settings

logger = logging.getLogger(__name__)

app = FastAPI(title="CCTV Employee Monitoring Dashboard", version="2.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global shared reference to the live monitoring pipeline state
class DashboardState:
    camera_manager = None
    monitoring_service = None
    grid_renderer = None
    latest_grid_jpeg: bytes | None = None
    latest_camera_jpegs: Dict[str, bytes] = {}
    fps_map: Dict[str, float] = {}
    start_time: datetime = datetime.now()

state = DashboardState()


def set_shared_pipeline(camera_mgr, monitoring_svc, grid_rend):
    """Called by main.py to share live stream references with the web app."""
    state.camera_manager = camera_mgr
    state.monitoring_service = monitoring_svc
    state.grid_renderer = grid_rend


def format_duration(seconds: float) -> str:
    """Format seconds into Xh Ym."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# ── Static Files & Dashboard Home ──────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_dashboard_html():
    """Serve the main dashboard Single Page Application."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard UI Initializing...</h1>")


# ── REST API Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_system_status():
    """Return live system health, camera counts, hardware engine, and uptime."""
    try:
        import torch
        if torch.backends.mps.is_available():
            hw_accel = "Apple Metal (MPS GPU)"
        elif torch.cuda.is_available():
            hw_accel = f"NVIDIA CUDA ({torch.cuda.get_device_name(0)})"
        else:
            hw_accel = "CPU (OpenVINO / ONNX)"
    except (ImportError, Exception):
        hw_accel = "Cloud Serverless (CPU)"


    cameras = []
    active_cameras = 0
    for cam_cfg in getattr(settings, "CAMERAS", []):
        cam_id = cam_cfg.get("id", "CAM")
        is_conn = state.camera_manager.is_connected(cam_id) if state.camera_manager else False
        fps = round(state.camera_manager.get_fps(cam_id), 1) if state.camera_manager else 0.0
        if is_conn:
            active_cameras += 1
        cameras.append({
            "id": cam_id,
            "name": cam_cfg.get("name", cam_id),
            "channel": cam_cfg.get("channel", 1),
            "connected": is_conn,
            "fps": fps
        })

    uptime_sec = int((datetime.now() - state.start_time).total_seconds())
    m, s = divmod(uptime_sec, 60)
    h, m = divmod(m, 60)
    uptime_str = f"{h}h {m}m {s}s"

    active_sessions_count = 0
    att_mgr = getattr(state.monitoring_service, "attendance_manager", getattr(state.monitoring_service, "attendance_mgr", None)) if state.monitoring_service else None
    if att_mgr and hasattr(att_mgr, "get_active_sessions"):
        active_sessions_count = len(att_mgr.get_active_sessions())

    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "hardware_engine": hw_accel,
        "uptime": uptime_str,
        "active_cameras": active_cameras,
        "total_cameras": len(cameras),
        "cameras": cameras,
        "active_workers_count": active_sessions_count,
        "playback_speed": getattr(settings, "PLAYBACK_SPEED", 1.0),
        "enable_playback": os.getenv("ENABLE_PLAYBACK", "0") not in ("0", "false", "False")
    }


@app.get("/api/attendance/live")
async def get_live_attendance():
    """Return all employees currently tracked on site in active sessions."""
    active_list = []
    att_mgr = getattr(state.monitoring_service, "attendance_manager", getattr(state.monitoring_service, "attendance_mgr", None)) if state.monitoring_service else None
    if att_mgr and hasattr(att_mgr, "get_active_sessions"):
        sessions = att_mgr.get_active_sessions()
        for sess in sessions:
            dur_sec = sess.working_duration
            active_list.append({
                "session_id": sess.session_id,
                "employee_id": sess.employee_id,
                "employee_name": sess.employee_name,
                "status": sess.status,
                "presence_status": getattr(sess, "presence_status", "IN"),
                "entry_time": sess.first_seen.strftime("%H:%M:%S"),
                "duration_seconds": round(dur_sec, 1),
                "formatted_duration": format_duration(dur_sec),
                "current_camera": getattr(sess, "last_camera_id", getattr(sess, "camera_id", "CAM001")),
                "phone_in_use": getattr(sess, "phone_confirmed_use_active", False),
                "phone_use_count": len(sess.phone_use_history),
                "recognition_confidence": round(sess.recognition_confidence, 1)
            })

    return {
        "count": len(active_list),
        "active_employees": active_list
    }


@app.get("/api/attendance/history")
async def get_attendance_history(
    date: Optional[str] = None,
    search: Optional[str] = None
):
    """Return historical attendance sessions formatted with entry, exit, duration and total hours."""
    known_dir = os.path.join(settings.OUTPUT_DIR, "known")
    all_files = glob.glob(os.path.join(known_dir, "attendance_*.json"))

    # Collect all available distinct dates across records
    available_dates = set()
    for fp in all_files:
        basename = os.path.basename(fp)
        parts = basename.replace(".json", "").split("_")
        if len(parts) >= 3:
            d_part = parts[2]
            if len(d_part) == 8 and d_part.isdigit():
                available_dates.add(f"{d_part[:4]}-{d_part[4:6]}-{d_part[6:]}")
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d_json = json.load(f)
            d_field = d_json.get("date")
            if d_field and len(d_field) == 10 and "-" in d_field:
                available_dates.add(d_field)
        except Exception:
            pass

    target_date_clean = date.replace("-", "").strip() if isinstance(date, str) and date.strip() and date.lower() not in ("all", "any", "") else None
    
    sessions_by_emp: Dict[str, Dict[str, Any]] = {}
    for filepath in all_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            emp_id = data.get("employee_id")
            if not emp_id or emp_id in ("Unknown", "UNKNOWN", "null") or emp_id.startswith("UNKNOWN") or emp_id.startswith("track_"):
                continue
            emp_name = data.get("employee_name", emp_id)
            basename = os.path.basename(filepath)
            parts = basename.replace(".json", "").split("_")
            fn_date = ""
            if len(parts) >= 3 and len(parts[2]) == 8 and parts[2].isdigit():
                fn_date = parts[2]

            raw_d = str(data.get("date") or "").strip()
            if raw_d and len(raw_d) == 10 and "-" in raw_d:
                rec_date = raw_d
            elif raw_d and len(raw_d) == 8 and raw_d.isdigit():
                rec_date = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:]}"
            elif fn_date:
                rec_date = f"{fn_date[:4]}-{fn_date[4:6]}-{fn_date[6:]}"
            else:
                rec_date = raw_d or datetime.now().strftime("%Y-%m-%d")

            rec_date_clean = rec_date.replace("-", "").strip()

            # Strict date filtering when date is selected
            if target_date_clean:
                if target_date_clean != rec_date_clean and target_date_clean != fn_date and target_date_clean not in basename:
                    continue

            # Filter by search term (employee name or ID)
            if isinstance(search, str) and search.strip():
                q = search.strip().lower()
                if q not in emp_id.lower() and q not in emp_name.lower():
                    continue

            key = f"{emp_id}_{rec_date}"
            if key not in sessions_by_emp:
                sessions_by_emp[key] = {
                    "employee_id": emp_id,
                    "employee_name": emp_name,
                    "date": rec_date,
                    "first_checkin": data.get("first_checkin", ""),
                    "last_checkout": data.get("last_checkout", ""),
                    "working_duration_seconds": float(data.get("working_duration_seconds", 0.0)),
                    "records": []
                }

            pres_status = data.get("presence_status", "OUT")
            entries_list = data.get("entries_and_exits", [])
            if entries_list:
                for v in entries_list:
                    dur_sec = float(v.get("duration_seconds", 0.0))
                    raw_exit = str(v.get("exit_time") or "").strip()
                    v_type = v.get("exit_type", data.get("exit_type"))
                    v_stat = v.get("status", "")
                    if raw_exit == "Video Ended" or v_type == "VIDEO_ENDED" or v_stat == "VIDEO_ENDED":
                        exit_display = "Video Ended"
                    elif not raw_exit or raw_exit in ("--:--", "", "None"):
                        exit_display = "--:--"
                    else:
                        exit_display = raw_exit[:8] if len(raw_exit) >= 8 and ":" in raw_exit else raw_exit

                    sessions_by_emp[key]["records"].append({
                        "entry_time": str(v.get("entry_time", "--:--"))[:8] if v.get("entry_time") else "--:--",
                        "exit_time": exit_display,
                        "duration_seconds": dur_sec,
                        "duration_formatted": v.get("duration", format_duration(dur_sec)),
                        "phone_seconds": data.get("phone_use_duration_seconds", 0.0),
                        "phone_count": data.get("phone_use_count", 0),
                        "productivity": data.get("productivity_score", 100.0),
                        "date": rec_date
                    })
            else:
                session_list = data.get("sessions") or [data]
                for s_item in session_list:
                    dur_sec = float(s_item.get("duration_seconds") or s_item.get("working_duration_seconds", 0.0))
                    in_t = s_item.get("first_checkin") or s_item.get("entry_time", "--:--")
                    out_t = s_item.get("last_checkout") or s_item.get("exit_time", "")
                    s_type = s_item.get("exit_type", data.get("exit_type"))
                    if str(out_t).strip() == "Video Ended" or s_type == "VIDEO_ENDED" or pres_status == "VIDEO_ENDED":
                        out_display = "Video Ended"
                    elif (not out_t or str(out_t).strip() in ("None", "--:--", "")) and pres_status == "IN":
                        out_display = "--:--"
                    elif out_t and str(out_t).strip() not in ("None", "--:--", ""):
                        out_display = str(out_t)[:8] if len(str(out_t)) >= 8 and ":" in str(out_t) else str(out_t)
                    else:
                        out_display = "--:--"
                    sessions_by_emp[key]["records"].append({
                        "entry_time": str(in_t)[:8] if in_t and in_t != "--:--" else "--:--",
                        "exit_time": out_display,
                        "duration_seconds": dur_sec,
                        "duration_formatted": format_duration(dur_sec),
                        "phone_seconds": s_item.get("phone_use_duration_seconds", 0.0),
                        "phone_count": s_item.get("phone_use_count", 0),
                        "productivity": s_item.get("productivity_score", 100.0),
                        "date": rec_date
                    })
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", filepath, exc)

    # Compute timeline rows matching user's exact required format
    timeline_rows = []
    grand_total_seconds = 0.0
    total_phone_seconds = 0.0
    total_phone_violations = 0

    for key, info in sessions_by_emp.items():
        records = info["records"]
        emp_phone_sec = 0.0

        for r in records:
            emp_phone_sec += r["phone_seconds"]
            total_phone_violations += r["phone_count"]
            timeline_rows.append({
                "type": "session",
                "employee_id": info["employee_id"],
                "employee_name": info["employee_name"],
                "date": info["date"],
                "entry": r["entry_time"],
                "entry_time": r["entry_time"],
                "exit": r["exit_time"],
                "exit_time": r["exit_time"],
                "duration": r["duration_formatted"],
                "formatted_duration": r["duration_formatted"],
                "duration_seconds": r["duration_seconds"],
                "phone_duration": format_duration(r["phone_seconds"]),
                "productivity": r["productivity"]
            })

        # Calculate employee's total working hours from first entry to last exit
        emp_total_sec = info.get("working_duration_seconds")
        if emp_total_sec is None or emp_total_sec <= 0:
            emp_total_sec = sum(r["duration_seconds"] for r in records)

        grand_total_seconds += emp_total_sec
        total_phone_seconds += emp_phone_sec

        # Subtotal row if multiple sessions exist
        if len(records) > 1:
            timeline_rows.append({
                "type": "subtotal",
                "employee_id": info["employee_id"],
                "employee_name": f"Total: {info['employee_name']}",
                "date": info["date"],
                "entry": info.get("first_checkin", records[0]["entry_time"] if records else ""),
                "entry_time": info.get("first_checkin", records[0]["entry_time"] if records else ""),
                "exit": info.get("last_checkout", records[-1]["exit_time"] if records else ""),
                "exit_time": info.get("last_checkout", records[-1]["exit_time"] if records else ""),
                "duration": format_duration(emp_total_sec),
                "formatted_duration": format_duration(emp_total_sec),
                "duration_seconds": emp_total_sec,
                "phone_duration": format_duration(emp_phone_sec),
                "productivity": round(max(0, 100 * (emp_total_sec - emp_phone_sec) / (emp_total_sec or 1)), 1)
            })

    grand_total_str = format_duration(grand_total_seconds)

    return {
        "date": date or "All Dates",
        "available_dates": sorted(list(available_dates), reverse=True),
        "total_records": len(timeline_rows),
        "total_employees": len(sessions_by_emp),
        "grand_total_working_hours": grand_total_str,
        "grand_total_seconds": grand_total_seconds,
        "total_phone_seconds": total_phone_seconds,
        "total_phone_violations": total_phone_violations,
        "timeline_rows": timeline_rows
    }


@app.get("/api/attendance/employee/{emp_id}")
async def get_employee_attendance_punches(emp_id: str, date: Optional[str] = None):
    """Return punch-card attendance pairs for an employee formatted for the client punch UI."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    date_str = date or today_str

    known_dir = os.path.join(settings.OUTPUT_DIR, "known")
    date_compact = date_str.replace("-", "")
    
    # Match candidate files
    matched_files = list(set(
        glob.glob(os.path.join(known_dir, f"attendance_{emp_id}_{date_compact}*.json")) +
        glob.glob(os.path.join(known_dir, f"attendance_{emp_id}_{date_str}*.json"))
    ))
    
    # If no files found for the exact selected date, fallback to the latest available file for this employee
    if not matched_files:
        all_emp_files = sorted(glob.glob(os.path.join(known_dir, f"attendance_{emp_id}_*.json")))
        if all_emp_files:
            matched_files = [all_emp_files[-1]]  # Use the latest available attendance file

    emp_name = emp_id
    try:
        from employee_management.employees import REGISTERED_EMPLOYEES
        if emp_id in REGISTERED_EMPLOYEES:
            emp_name = REGISTERED_EMPLOYEES[emp_id].get("name", emp_id)
    except Exception:
        pass

    punches = []
    total_duration_sec = 0.0
    actual_record_date = date_str

    def _format_time_str(t_str: str) -> str:
        if not t_str:
            return ""
        try:
            if "T" in t_str:
                return datetime.fromisoformat(t_str).strftime("%I:%M %p")
            clean_t = t_str.split(".")[0]
            return datetime.strptime(clean_t, "%H:%M:%S").strftime("%I:%M %p")
        except Exception:
            return t_str

    # If live monitoring is running and this employee has an active session, use the live session state directly
    att_mgr = getattr(state.monitoring_service, "attendance_manager", getattr(state.monitoring_service, "attendance_mgr", None)) if state.monitoring_service else None
    active_sess = None
    if att_mgr and hasattr(att_mgr, "get_active_sessions"):
        for sess in att_mgr.get_active_sessions():
            if sess.employee_id == emp_id and getattr(sess, "presence_status", "IN") == "IN":
                active_sess = sess
                emp_name = sess.employee_name or emp_name
                break

    if active_sess is not None:
        dur_sec = active_sess.working_duration
        total_duration_sec = dur_sec
        actual_record_date = active_sess.first_seen.strftime("%Y-%m-%d")
        punches = [{
            "in_time": active_sess.first_seen.strftime("%I:%M %p"),
            "in_location": "KANCHIPURAM",
            "in_camera": getattr(active_sess, "entrance_camera_id", "CAM001"),
            "in_method": "Face Biometrics",
            "out_time": "In Progress",
            "out_location": "KANCHIPURAM",
            "out_camera": getattr(active_sess, "last_camera_id", "CAM001"),
            "out_method": "Live Tracking",
            "duration_formatted": format_duration(dur_sec),
            "duration_seconds": dur_sec,
            "status": "ACTIVE"
        }]
    else:
        # Load from completed daily JSON file
        for filepath in sorted(matched_files):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                emp_name = data.get("employee_name", emp_name)
                actual_record_date = data.get("date", actual_record_date)
                total_duration_sec = float(data.get("working_duration_seconds", 0.0))
                
                if "entries_and_exits" in data and isinstance(data["entries_and_exits"], list) and len(data["entries_and_exits"]) > 0:
                    for visit in data["entries_and_exits"]:
                        entry_raw = visit.get("entry_time", "")
                        exit_raw = str(visit.get("exit_time") or "").strip()
                        v_dur_sec = float(visit.get("duration_seconds", 0.0))
                        v_type = visit.get("exit_type", data.get("exit_type"))
                        v_stat = visit.get("status", "")

                        if exit_raw == "Video Ended" or v_type == "VIDEO_ENDED" or v_stat == "VIDEO_ENDED":
                            out_display = "Video Ended"
                            punch_status = "VIDEO_ENDED"
                            out_method = "Video Ended"
                        elif exit_raw and exit_raw not in ("--:--", "", "None", "In Progress"):
                            out_display = _format_time_str(exit_raw)
                            punch_status = "COMPLETED"
                            out_method = "Face Biometrics"
                        else:
                            out_display = "In Progress"
                            punch_status = "ACTIVE"
                            out_method = "Live Tracking"
                        
                        punches.append({
                            "in_time": _format_time_str(entry_raw),
                            "in_location": "KANCHIPURAM",
                            "in_camera": visit.get("entrance_camera", data.get("entrance_camera_id", "CAM001")),
                            "in_method": "Face Biometrics",
                            "out_time": out_display,
                            "out_location": "KANCHIPURAM",
                            "out_camera": visit.get("exit_camera", data.get("exit_camera_id", "CAM001")),
                            "out_method": out_method,
                            "duration_formatted": visit.get("duration", format_duration(v_dur_sec)),
                            "duration_seconds": v_dur_sec,
                            "status": punch_status,
                            "_sort_key": entry_raw
                        })
                elif "entry_time" in data:
                    entry_raw = data["entry_time"]
                    exit_raw = str(data.get("exit_time") or "").strip()
                    last_chk = str(data.get("last_checkout") or "").strip()
                    v_dur_sec = float(data.get("working_duration_seconds", 0.0))
                    v_type = data.get("exit_type")
                    
                    if exit_raw == "Video Ended" or last_chk == "Video Ended" or v_type == "VIDEO_ENDED":
                        out_display = "Video Ended"
                        punch_status = "VIDEO_ENDED"
                        out_method = "Video Ended"
                    elif exit_raw and exit_raw not in ("--:--", "", "None", "In Progress"):
                        out_display = _format_time_str(exit_raw)
                        punch_status = "COMPLETED"
                        out_method = "Face Biometrics"
                    else:
                        out_display = "In Progress"
                        punch_status = "ACTIVE"
                        out_method = "Live Tracking"

                    punches.append({
                        "in_time": _format_time_str(entry_raw),
                        "in_location": "KANCHIPURAM",
                        "in_camera": data.get("entrance_camera_id", "CAM001"),
                        "in_method": "Face Biometrics",
                        "out_time": out_display,
                        "out_location": "KANCHIPURAM",
                        "out_camera": data.get("exit_camera_id", "CAM001"),
                        "out_method": out_method,
                        "duration_formatted": format_duration(v_dur_sec),
                        "duration_seconds": v_dur_sec,
                        "status": punch_status,
                        "_sort_key": entry_raw
                    })
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", filepath, exc)

    # Sort punches chronologically
    punches.sort(key=lambda p: p.get("_sort_key", ""))
    for p in punches:
        p.pop("_sort_key", None)

    # Build human date heading
    try:
        if "-" in actual_record_date:
            dt_obj = datetime.strptime(actual_record_date, "%Y-%m-%d")
        else:
            dt_obj = datetime.strptime(actual_record_date, "%Y%m%d")
        date_heading = dt_obj.strftime("%a, %d %b %Y")
    except Exception:
        date_heading = actual_record_date

    return {
        "employee_id": emp_id,
        "employee_name": emp_name,
        "date": actual_record_date,
        "date_heading": date_heading,
        "total_working_hours": format_duration(total_duration_sec),
        "total_working_seconds": total_duration_sec,
        "punch_count": len(punches),
        "punches": punches
    }


@app.get("/api/employees")
async def get_enrolled_employees():
    """Return all enrolled employees in the employee registry with photos and presence status."""
    emp_list = []
    
    # Map human names from static registry
    static_name_map = {}
    try:
        from employee_management.employees import EMPLOYEES
        for e in EMPLOYEES:
            if isinstance(e, dict) and "employee_id" in e:
                static_name_map[e["employee_id"]] = e.get("name", e["employee_id"])
    except Exception:
        pass

    # Try using EmployeeManager for comprehensive scanning
    try:
        from employee_management.employee_manager import EmployeeManager
        emp_mgr = EmployeeManager()
        emp_mgr.load_employees()
        loaded_emps = emp_mgr.get_all_employees()
        
        # Get live active employee IDs on camera
        active_ids = set()
        try:
            live_status = await get_live_attendance()
            for ae in live_status.get("active_employees", []):
                active_ids.add(ae.get("employee_id"))
        except Exception:
            pass

        for emp in loaded_emps:
            emp_id = emp["employee_id"]
            emp_name = static_name_map.get(emp_id, emp.get("name", emp_id))
            status = emp.get("status", "Active")
            images = emp_mgr.get_employee_images(emp_id)
            sample_img = None
            if images:
                first_img = os.path.basename(images[0])
                sample_img = f"/api/employee_image/{emp_id}/{first_img}"
            
            emp_list.append({
                "id": emp_id,
                "name": emp_name,
                "status": status,
                "image_count": len(images),
                "sample_image": sample_img,
                "is_on_site": emp_id in active_ids
            })
    except Exception as e:
        logger.error(f"Error loading employees via EmployeeManager: {e}")
        # Fallback to direct directory scanning
        base_dir = os.path.join(os.path.dirname(__file__), "..", "employee_images")
        if os.path.exists(base_dir):
            for entry in sorted(os.listdir(base_dir)):
                emp_folder = os.path.join(base_dir, entry)
                if os.path.isdir(emp_folder):
                    images = [f for f in os.listdir(emp_folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
                    emp_list.append({
                        "id": entry,
                        "name": static_name_map.get(entry, entry.replace("_", " ")),
                        "status": "Active",
                        "image_count": len(images),
                        "sample_image": f"/api/employee_image/{entry}/{images[0]}" if images else None,
                        "is_on_site": False
                    })

    return {"employees": emp_list, "total_count": len(emp_list)}


@app.get("/api/employee_image/{emp_id}/{filename}")
async def get_employee_image(emp_id: str, filename: str):
    """Serve an enrolled employee photo."""
    img_path = os.path.join(os.path.dirname(__file__), "..", "employee_images", emp_id, filename)
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            media_type = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
            return Response(content=f.read(), media_type=media_type)
    raise HTTPException(status_code=404, detail="Image not found")


@app.get("/api/employee_avatar/{emp_id}")
async def get_employee_avatar(emp_id: str):
    """Serve the primary enrolled avatar photo for an employee."""
    base_dir = os.path.join(os.path.dirname(__file__), "..", "employee_images", emp_id)
    if os.path.isdir(base_dir):
        images = [f for f in os.listdir(base_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        if images:
            img_path = os.path.join(base_dir, sorted(images)[0])
            with open(img_path, "rb") as f:
                media_type = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
                return Response(content=f.read(), media_type=media_type)
    raise HTTPException(status_code=404, detail="Avatar not found")


@app.get("/api/export/csv")
@app.get("/api/attendance/export/csv")
async def export_attendance_csv(date: Optional[str] = Query(None)):
    """Export daily attendance timeline as a downloadable CSV file."""
    data = await get_attendance_history(date=date)
    rows = data.get("timeline_rows", [])
    date_str = data.get("date", "today")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Employee Name", "Entry Time", "Exit Time", "Duration", "Phone Usage", "Productivity %"])

    for r in rows:
        if r.get("type") == "session":
            writer.writerow([
                r.get("employee_id", ""),
                r.get("employee_name", ""),
                r.get("entry", ""),
                r.get("exit", ""),
                r.get("duration", ""),
                r.get("phone_duration", ""),
                f"{r.get('productivity', 100)}%"
            ])
        elif r.get("type") == "subtotal":
            writer.writerow([
                "",
                r.get("employee_name", ""),
                "",
                "",
                r.get("duration", ""),
                "",
                ""
            ])

    writer.writerow(["", "", "", "", "", "", ""])
    writer.writerow(["TOTAL WORKING HOURS", "", "", "", data.get("grand_total_working_hours", "0h 00m"), "", ""])

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance_report_{date_str}.csv"}
    )


# ── MJPEG Live Video Streaming ────────────────────────────────────────────────

def mjpeg_generator(cam_id: Optional[str] = None):
    """Continuously yield JPEG frames for live browser display."""
    target_fps = int(getattr(settings, "TARGET_FPS", 25))
    sleep_interval = 1.0 / max(5, target_fps)

    while True:
        frame_bytes = None

        if cam_id and cam_id in state.latest_camera_jpegs:
            frame_bytes = state.latest_camera_jpegs[cam_id]
        elif state.latest_grid_jpeg:
            frame_bytes = state.latest_grid_jpeg

        if frame_bytes is None:
            # Generate a clean dark fallback frame if pipeline has not sent frames yet
            dummy = cv2.imread(os.path.join(os.path.dirname(__file__), "..", "captures", "placeholder.jpg"))
            if dummy is None:
                dummy = 25 * (cv2.imread(os.path.join(STATIC_DIR, "placeholder.jpg")) if os.path.exists(os.path.join(STATIC_DIR, "placeholder.jpg")) else 0)
            if dummy is None or not isinstance(dummy, cv2.Mat):
                import numpy as np
                dummy = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(dummy, "Connecting to CCTV Stream...", (140, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 2)
            _, encoded = cv2.imencode(".jpg", dummy, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_bytes = encoded.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )
        time.sleep(sleep_interval)


@app.get("/video_feed/grid")
async def video_feed_grid():
    """Live multi-camera grid video stream."""
    return StreamingResponse(
        mjpeg_generator(cam_id=None),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/video_feed/{camera_id}")
async def video_feed_camera(camera_id: str):
    """Live stream for a specific individual camera."""
    return StreamingResponse(
        mjpeg_generator(cam_id=camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn
    host = getattr(settings, "DASHBOARD_HOST", "0.0.0.0")
    port = int(getattr(settings, "DASHBOARD_PORT", 8000))
    print(f"\n=======================================================")
    print(f"  AI CCTV MONITORING & ATTENDANCE DASHBOARD")
    print(f"  URL: http://localhost:{port}")
    print(f"=======================================================\n")
    uvicorn.run(app, host=host, port=port)
