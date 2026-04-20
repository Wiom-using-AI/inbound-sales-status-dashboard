import os, json, urllib.request, urllib.parse, csv, sys
from pathlib import Path

# Load API key: env var first, then C:\credentials\.env fallback
API_KEY = os.environ.get("METABASE_API_KEY")
if not API_KEY:
    env_path = Path(r"C:\credentials\.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "METABASE_API_KEY":
                API_KEY = v.strip()
                break
if not API_KEY:
    print("ERROR: METABASE_API_KEY not found. Set it as env var or in C:\\credentials\\.env")
    sys.exit(1)
URL = "https://metabase.wiom.in/api/dataset/csv"

def run_query(sql, out_path):
    """Run a Metabase native SQL query and save the CSV result."""
    query_json = json.dumps({
        "database": 113,
        "type": "native",
        "native": {"query": sql},
    })
    body = urllib.parse.urlencode({"query": query_json}).encode()
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={
            "x-api-key": API_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        csv_bytes = r.read()
    # Guard: if Metabase returns a JSON error, don't overwrite good data
    text = csv_bytes.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            err = json.loads(text)
            msg = err.get("error", text[:200])
        except Exception:
            msg = text[:200]
        print(f"  ERROR from Metabase: {msg}")
        print("  Keeping existing data file unchanged.")
        sys.exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(csv_bytes)
    with out_path.open("r", encoding="utf-8") as f:
        n = sum(1 for _ in f) - 1
    print(f"  Rows written: {n}")
    print(f"  Wrote {out_path}")

# ---- Query 1: Disposition counts ----
# Before April 2026: Sales Queue + Booking Queue
# From April 2026 onwards: Sales Queue only
SQL_COUNTS = """
WITH base AS (
  SELECT
    CALL_TIME::DATE AS call_date,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), '(Unclassified)') AS disposition_class,
    COALESCE(NULLIF(TRIM(DISPOSITION_CODE),  ''), '(Unclassified)') AS disposition_code
  FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
  WHERE CALL_TYPE = 'inbound.call.dial'
    AND (
      (CALL_TIME::DATE < '2026-04-01' AND QUEUE_NAME IN ('sales_queue', 'booking_queue'))
      OR
      (CALL_TIME::DATE >= '2026-04-01' AND QUEUE_NAME = 'sales_queue')
    )
)
SELECT call_date, disposition_class, disposition_code, COUNT(*) AS call_count
FROM base
WHERE call_date >= '2026-02-01'
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

# ---- Query 2: Daily metrics (AHT, total calls, missed calls) ----
SQL_METRICS = """
WITH base AS (
  SELECT
    CALL_TIME::DATE AS call_date,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), '(Unclassified)') AS disposition_class,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(USER_TALK_TIME))  AS talk_sec,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(ACW_DURATION))    AS acw_sec,
    USER_ID
  FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
  WHERE CALL_TYPE = 'inbound.call.dial'
    AND (
      (CALL_TIME::DATE < '2026-04-01' AND QUEUE_NAME IN ('sales_queue', 'booking_queue'))
      OR
      (CALL_TIME::DATE >= '2026-04-01' AND QUEUE_NAME = 'sales_queue')
    )
)
SELECT
  call_date,
  COUNT(*)                                             AS total_calls,
  SUM(CASE WHEN disposition_class = '(Unclassified)' THEN 1 ELSE 0 END)  AS missed_calls,
  AVG(CASE WHEN disposition_class != '(Unclassified)'
      THEN COALESCE(talk_sec, 0) + COALESCE(acw_sec, 0)
      END)                                             AS avg_aht_sec,
  COUNT(DISTINCT USER_ID)                              AS agents_logged
FROM base
WHERE call_date >= '2026-02-01'
GROUP BY 1
ORDER BY 1
"""

data_dir = Path(os.environ.get("DATA_DIR",
            str(Path(__file__).resolve().parent.parent / "data")))

print("Pulling disposition counts (Sales & Status Queue)...")
run_query(SQL_COUNTS, data_dir / "sales_daily.csv")

print("Pulling daily metrics (Sales & Status Queue)...")
run_query(SQL_METRICS, data_dir / "sales_metrics.csv")
