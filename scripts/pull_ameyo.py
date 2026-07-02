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

# ---- Query 1: Disposition counts (existing) ----
SQL_COUNTS = """
WITH base AS (
  SELECT
    CALL_TIME::DATE AS call_date,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), 'Missed') AS disposition_class,
    COALESCE(NULLIF(TRIM(DISPOSITION_CODE),  ''), 'Missed') AS disposition_code
  FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
  WHERE QUEUE_NAME IN ('high_pain_queue', 'low_pain_queue')
    AND CALL_TYPE = 'inbound.call.dial'
)
SELECT call_date, disposition_class, disposition_code, COUNT(*) AS call_count
FROM base
WHERE call_date >= '2026-02-01'
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

# ---- Query 2: Daily metrics (AHT, total calls, missed calls) ----
# USER_TALK_TIME and ACW_DURATION are stored as 'HH:MM:SS' strings in Snowflake.
# Convert to seconds using TIME_TO_SECONDS (Snowflake: EXTRACT or DATEDIFF).
SQL_METRICS = """
WITH base AS (
  SELECT
    CALL_TIME::DATE AS call_date,
    QUEUE_NAME,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), 'Missed') AS disposition_class,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(USER_TALK_TIME))  AS talk_sec,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(ACW_DURATION))    AS acw_sec,
    USER_ID
  FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
  WHERE QUEUE_NAME IN ('high_pain_queue', 'low_pain_queue')
    AND CALL_TYPE = 'inbound.call.dial'
)
SELECT
  call_date,
  COUNT(*)                                                              AS total_calls,
  SUM(CASE WHEN disposition_class = 'Missed' THEN 1 ELSE 0 END)       AS missed_calls,
  AVG(CASE WHEN talk_sec > 0
      THEN talk_sec + COALESCE(acw_sec, 0)
      END)                                                              AS avg_aht_sec,
  COUNT(DISTINCT USER_ID)                                               AS agents_logged,
  SUM(CASE WHEN queue_name = 'low_pain_queue' THEN 1 ELSE 0 END)      AS low_pain_calls
FROM base
WHERE call_date >= '2026-02-01'
GROUP BY 1
ORDER BY 1
"""


# ---- Query 3: Hourly slot data for last 8 days (Current tab) ----
SQL_HOURLY = """
SELECT
    CALL_TIME::DATE                                                    AS call_date,
    HOUR(CALL_TIME)                                                    AS call_hour,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), 'Missed')   AS disposition_class,
    COALESCE(NULLIF(TRIM(DISPOSITION_CODE),  ''), 'Missed')   AS disposition_code,
    COUNT(*)                                                           AS call_count
FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
WHERE QUEUE_NAME IN ('high_pain_queue', 'low_pain_queue')
  AND CALL_TYPE = 'inbound.call.dial'
  AND CALL_TIME::DATE >= DATEADD(day, -8, CURRENT_DATE())
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4
"""

data_dir = Path(os.environ.get("DATA_DIR",
            str(Path(__file__).resolve().parent.parent / "data")))

SQL_CSAT = """
SELECT
  TRY_TO_TIMESTAMP(CALL_DATE_AND_TIME, 'MM/DD/YY HH12:MI AM')::DATE AS call_date,
  COUNT(*)                                                             AS total_surveyed,
  SUM(CASE WHEN FEEDBACK_INPUT NOT IN ('No Input', 'Invalid Input')
            AND FEEDBACK_INPUT IS NOT NULL
            AND TRIM(FEEDBACK_INPUT) != ''
       THEN 1 ELSE 0 END)                                             AS total_responded,
  SUM(CASE WHEN FEEDBACK_INPUT IN ('4', '5') THEN 1 ELSE 0 END)      AS csat_satisfied
FROM PROD_DB.PUBLIC.AMEYO_CSAT_REPORT_TABLE
WHERE SOURCE_CAMPAIGN = 'customerSupportInbound'
  AND QUEUE_NAME IN ('high_pain_queue', 'low_pain_queue')
  AND TRY_TO_TIMESTAMP(CALL_DATE_AND_TIME, 'MM/DD/YY HH12:MI AM')::DATE >= '2026-02-01'
GROUP BY 1
ORDER BY 1
"""

SQL_REPEAT = """
WITH per_customer AS (
  SELECT
    CALL_TIME::DATE        AS call_date,
    TRIM(PHONE)            AS phone,
    COUNT(*)               AS call_count
  FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
  WHERE QUEUE_NAME IN ('high_pain_queue', 'low_pain_queue')
    AND CALL_TYPE = 'inbound.call.dial'
    AND PHONE IS NOT NULL
    AND TRIM(PHONE) != ''
    AND CALL_TIME::DATE >= '2026-02-01'
  GROUP BY 1, 2
)
SELECT
  call_date,
  COUNT(*)                                                     AS unique_callers,
  SUM(CASE WHEN call_count > 1 THEN 1 ELSE 0 END)             AS repeat_callers
FROM per_customer
GROUP BY 1
ORDER BY 1
"""

print("Pulling disposition counts...")
run_query(SQL_COUNTS, data_dir / "ameyo_daily.csv")

print("Pulling daily metrics (AHT, missed calls)...")
run_query(SQL_METRICS, data_dir / "ameyo_metrics.csv")

print("Pulling hourly slot data (Current tab)...")
run_query(SQL_HOURLY, data_dir / "ameyo_hourly.csv")

print("Pulling CSAT data (Service Queue)...")
run_query(SQL_CSAT, data_dir / "service_csat.csv")

print("Pulling repeat caller data (Service Queue)...")
run_query(SQL_REPEAT, data_dir / "ameyo_repeat.csv")
