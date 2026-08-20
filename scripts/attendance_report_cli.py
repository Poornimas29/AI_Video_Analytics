# scripts/attendance_report_cli.py
"""CLI utility to print and export daily employee attendance timeline tables.

Usage:
    python scripts/attendance_report_cli.py             # Today's table
    python scripts/attendance_report_cli.py 20260716    # Specific date (YYYYMMDD)
"""

import sys
import os
from datetime import datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from session.attendance_manager import AttendanceManager

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    mgr = AttendanceManager()
    
    print(f"\n=======================================================")
    print(f"       EMPLOYEE ATTENDANCE TIMELINE REPORT ({date_str})")
    print(f"=======================================================\n")
    
    table_output = mgr.generate_markdown_timeline_table(date_str)
    print(table_output)
    print("\n=======================================================\n")

if __name__ == "__main__":
    main()
