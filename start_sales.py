"""
Railway startup script — Inbound Summary: Sales & Status Queue.
Pulls data from Metabase, builds the HTML dashboard, then serves it.
A background thread re-pulls + rebuilds every day at 08:30 IST.
"""
import subprocess
import sys
import os
import threading
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "scripts")
IST = timezone(timedelta(hours=5, minutes=30))
REFRESH_HOUR   = 8
REFRESH_MINUTE = 30


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
    """Pull fresh data and rebuild the Sales & Status Queue dashboard."""
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST] "
          "Starting daily refresh (Sales & Status Queue)...", flush=True)
    run("pull_ameyo_sales.py")
    run("build_html_sales.py")
    try:
        run("build_dashboard_sales.py")
    except Exception as e:
        print(f"Excel build skipped: {e}", flush=True)
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST] "
          "Daily refresh complete.", flush=True)


def scheduler_loop():
    """Sleep until the next 08:30 IST, then refresh and repeat."""
    while True:
        now = datetime.now(IST)
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


# --- Initial data pull on startup ---
refresh()

# --- Start background scheduler ---
t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

# --- Start server on PORT provided by Railway ---
print("=== Starting Sales & Status Queue server ===", flush=True)
os.environ["HOST"] = "0.0.0.0"
os.environ["DASHBOARD_ROOT"] = os.path.join(BASE, "output", "web_sales")
exec(open(os.path.join(SCRIPTS, "serve_sales_dashboard.py")).read())
