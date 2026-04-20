"""
Railway startup script.
Reads DASHBOARD_MODE env var to decide which dashboard to serve:
  - "sales"   → Sales & Status Queue  (pull_ameyo_sales, build_html_sales, web_sales/)
  - (default) → Service Queue         (pull_ameyo, build_html, web/)

A background thread re-pulls + rebuilds daily at the configured IST time.
"""
import subprocess
import sys
import os
import threading
import time
from datetime import datetime, timedelta, timezone

BASE    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "scripts")
IST     = timezone(timedelta(hours=5, minutes=30))

MODE = os.environ.get("DASHBOARD_MODE", "service").lower()

if MODE == "sales":
    PULL_SCRIPT    = "pull_ameyo_sales.py"
    HTML_SCRIPT    = "build_html_sales.py"
    EXCEL_SCRIPT   = "build_dashboard_sales.py"
    SERVE_SCRIPT   = "serve_sales_dashboard.py"
    DASHBOARD_ROOT = os.path.join(BASE, "output", "web_sales")
    REFRESH_HOUR   = 8
    REFRESH_MINUTE = 30
    LABEL          = "Sales & Status Queue"
else:
    PULL_SCRIPT    = "pull_ameyo.py"
    HTML_SCRIPT    = "build_html.py"
    EXCEL_SCRIPT   = "build_dashboard.py"
    SERVE_SCRIPT   = "serve_dashboard.py"
    DASHBOARD_ROOT = os.path.join(BASE, "output", "web")
    REFRESH_HOUR   = 8
    REFRESH_MINUTE = 0
    LABEL          = "Service Queue"


def run(script):
    print(f"=== Running {script} ===", flush=True)
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script)],
        cwd=BASE,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}", flush=True)


def refresh():
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST] "
          f"Starting daily refresh ({LABEL})...", flush=True)
    run(PULL_SCRIPT)
    run(HTML_SCRIPT)
    try:
        run(EXCEL_SCRIPT)
    except Exception as e:
        print(f"Excel build skipped: {e}", flush=True)
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST] "
          "Daily refresh complete.", flush=True)


def scheduler_loop():
    while True:
        now    = datetime.now(IST)
        target = now.replace(hour=REFRESH_HOUR, minute=REFRESH_MINUTE,
                             second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        print(f"[scheduler] Next refresh at {target.strftime('%Y-%m-%d %H:%M')} IST "
              f"(in {wait_sec/3600:.1f}h)", flush=True)
        time.sleep(wait_sec)
        try:
            refresh()
        except Exception as e:
            print(f"[scheduler] Refresh failed: {e}", flush=True)


# --- Initial data pull ---
refresh()

# --- Background scheduler ---
threading.Thread(target=scheduler_loop, daemon=True).start()

# --- Start server ---
print(f"=== Starting {LABEL} server ===", flush=True)
os.environ["HOST"]           = "0.0.0.0"
os.environ["DASHBOARD_ROOT"] = DASHBOARD_ROOT
exec(open(os.path.join(SCRIPTS, SERVE_SCRIPT)).read())
