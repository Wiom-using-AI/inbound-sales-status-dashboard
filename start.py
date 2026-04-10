"""
Railway startup script.
Pulls data from Metabase, builds the HTML dashboard, then serves it.
"""
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "scripts")

def run(script):
    print(f"=== Running {script} ===", flush=True)
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script)],
        cwd=BASE,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}", flush=True)

# Step 1: Pull data from Metabase
run("pull_ameyo.py")

# Step 2: Build HTML dashboard
run("build_html.py")

# Step 3: Build Excel dashboard (optional, non-blocking)
try:
    run("build_dashboard.py")
except Exception as e:
    print(f"Excel build skipped: {e}", flush=True)

# Step 4: Serve the dashboard
print("=== Starting server ===", flush=True)
os.environ["HOST"] = "0.0.0.0"
exec(open(os.path.join(SCRIPTS, "serve_dashboard.py")).read())
