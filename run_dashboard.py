#!/usr/bin/env python3
"""run_dashboard.py — Standalone Web Dashboard Server for AI CCTV Attendance & Monitoring.

Allows managers and administrators to view live presence, punch cards,
daily attendance history, registered employees, and download CSV reports
at any time without needing to run the full video analysis pipeline.

Usage:
    python run_dashboard.py
    # Open http://localhost:8000 in any browser
"""

import os
import sys
import uvicorn

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config.settings as settings
from web_dashboard.app import app

def main():
    host = getattr(settings, "DASHBOARD_HOST", "0.0.0.0")
    port = int(getattr(settings, "DASHBOARD_PORT", 8000))

    print("\n=======================================================")
    print("  🚀 AI CCTV ATTENDANCE & MONITORING WEB DASHBOARD")
    print(f"  🌐 Open in Browser: http://localhost:{port}")
    print("=======================================================")
    print("  • View real-time & historical employee attendance")
    print("  • Interactive employee punch cards & photos")
    print("  • Download CSV audit reports")
    print("  • Press Ctrl+C to stop the dashboard server\n")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )

if __name__ == "__main__":
    main()
