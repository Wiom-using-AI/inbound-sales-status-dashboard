"""
Local dashboard server.

- Serves the static dashboard from output/web/
- Adds one dynamic endpoint: /raw?cls=...&code=...&scope=day|mtd|prev&date=YYYY-MM-DD&ym=YYYY-MM
  which runs a Metabase SQL query against
  PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT and returns the matching raw calls.

Run:   python scripts/serve_dashboard.py
Open:  http://localhost:8765/
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("DASHBOARD_ROOT",
            str(Path(__file__).resolve().parent.parent / "output" / "web")))
PORT = int(os.environ.get("PORT", 8765))
METABASE_URL = "https://metabase.wiom.in/api/dataset"
DATABASE_ID = 113

# -------- load API key --------
# Priority: environment variable > C:\credentials\.env
API_KEY = os.environ.get("METABASE_API_KEY")
if not API_KEY:
    env_path = Path(r"C:\credentials\.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == "METABASE_API_KEY":
                    API_KEY = v.strip()
                    break
if not API_KEY:
    print("WARNING: METABASE_API_KEY not set — drill-through will fail.")

# -------- Sales Queue rollup: all source classes ----------
SALES_SOURCE_CLASSES = [
    "Sales-App Issues",
    "Sales-Next Steps",
    "Sales-Next Steps - Old Contruct",
    "Sales-Pause",
    "Sales-System Understanding",
    "Sales-System Understanding - Old",
]

# columns we return to the browser
RAW_COLS = [
    "CALL_ID", "CALL_TIME", "QUEUE_NAME", "PHONE",
    "USER_NAME", "DISPOSITION_CLASS", "DISPOSITION_CODE",
    "CUSTOMER_TALK_TIME", "HANGUP_DETAILS", "CALL_NOTES",
]

def sql_quote(s: str) -> str:
    return s.replace("'", "''")


def build_where(cls: str, code: str, scope: str, day: str, ym: str) -> str:
    """Return a SQL WHERE clause (without the leading WHERE)."""
    wh = [
        "QUEUE_NAME IN ('high_pain_queue','low_pain_queue')",
        "CALL_TYPE = 'inbound.call.dial'",
    ]

    # --- date filter ---
    if scope == "day" and day:
        wh.append(
            "CALL_TIME::DATE "
            f"= DATE '{day}'"
        )
    elif scope in ("mtd", "prev") and ym:
        y, m = ym.split("-")
        # first of month
        start = date(int(y), int(m), 1)
        # first of next month
        if int(m) == 12:
            end = date(int(y) + 1, 1, 1)
        else:
            end = date(int(y), int(m) + 1, 1)
        wh.append(
            "CALL_TIME::DATE "
            f">= DATE '{start.isoformat()}' AND "
            "CALL_TIME::DATE "
            f"<  DATE '{end.isoformat()}'"
        )

    # --- class / code filter ---
    if cls == "Sales Queue":
        q = ",".join("'" + sql_quote(c) + "'" for c in SALES_SOURCE_CLASSES)
        wh.append(f"DISPOSITION_CLASS IN ({q})")
    elif cls == "(Unclassified)":
        wh.append("(DISPOSITION_CLASS IS NULL OR TRIM(DISPOSITION_CLASS) = '')")
    elif cls == "Booking Queue":
        wh.append(f"DISPOSITION_CLASS = 'Booking Queue'")
    elif cls:
        wh.append(f"DISPOSITION_CLASS = '{sql_quote(cls)}'")

    if code and cls not in ("Sales Queue", "Booking Queue", "(Unclassified)"):
        wh.append(f"DISPOSITION_CODE = '{sql_quote(code)}'")

    return " AND ".join(wh)


def metabase_query_csv(sql: str):
    """Use /api/dataset/csv to get ALL rows (no 2000-row cap)."""
    import csv as csv_mod, io
    csv_url = METABASE_URL.replace("/api/dataset", "/api/dataset/csv")
    payload = json.dumps({
        "database": DATABASE_ID,
        "type": "native",
        "native": {"query": sql},
    }).encode()
    req = urllib.request.Request(
        csv_url,
        data=payload,
        method="POST",
        headers={
            "x-api-key": API_KEY or "",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read().decode("utf-8", errors="replace")
    # Check for JSON error response
    if raw.strip().startswith("{"):
        err = json.loads(raw)
        raise RuntimeError(err.get("error", raw[:200]))
    reader = csv_mod.reader(io.StringIO(raw))
    headers = next(reader)
    rows = [row for row in reader]
    return headers, rows


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/raw":
            return self.handle_raw(parsed)
        if parsed.path == "/refresh":
            return self.handle_refresh()
        return super().do_GET()

    def handle_refresh(self):
        """Trigger a manual data refresh (pull + rebuild)."""
        import subprocess, threading
        base = Path(os.environ.get("DASHBOARD_ROOT",
                    str(Path(__file__).resolve().parent.parent / "output" / "web")))
        project_root = base.parent.parent  # output/web -> output -> project
        scripts_dir = project_root / "scripts"

        def do_refresh():
            for script in ["pull_ameyo.py", "build_html.py"]:
                subprocess.run(
                    [sys.executable, str(scripts_dir / script)],
                    cwd=str(project_root),
                )
            print("[manual refresh] Complete.", flush=True)

        threading.Thread(target=do_refresh, daemon=True).start()
        return self.send_json(200, {"status": "Refresh started. Page will update in ~30 seconds."})

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_raw(self, parsed):
        if not API_KEY:
            return self.send_json(500, {"error": "METABASE_API_KEY not configured on server."})
        q = urllib.parse.parse_qs(parsed.query)
        cls   = (q.get("cls",   [""])[0] or "").strip()
        code  = (q.get("code",  [""])[0] or "").strip()
        scope = (q.get("scope", [""])[0] or "").strip()
        day   = (q.get("date",  [""])[0] or "").strip()
        ym    = (q.get("ym",    [""])[0] or "").strip()

        where = build_where(cls, code, scope, day, ym)
        cols_sql = ", ".join(RAW_COLS)
        sql = (
            f"SELECT {cols_sql} FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT "
            f"WHERE {where} ORDER BY CALL_TIME DESC"
        )

        try:
            headers, rows = metabase_query_csv(sql)
        except Exception as e:
            return self.send_json(500, {"error": f"Metabase request failed: {e}"})

        return self.send_json(200, {
            "cols": headers,
            "rows": rows,
            "count": len(rows),
            "sql": sql,
        })


if __name__ == "__main__":
    os.chdir(str(ROOT))
    host = os.environ.get("HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, PORT), Handler)
    print(f"Wiom Inbound Call Dashboard — http://localhost:{PORT}/")
    print(f"Root: {ROOT}")
    print("Press Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
