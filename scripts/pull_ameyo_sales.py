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
    CALL_TIME::DATE                                                  AS call_date,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), 'Missed') AS disposition_class,
    COALESCE(NULLIF(TRIM(DISPOSITION_CODE),  ''), 'Missed') AS disposition_code
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
    CALL_TIME::DATE                                                       AS call_date,
    -- Missed = customer never reached an agent (USER_TALK_TIME is NULL or 00:00:00)
    -- Matches Ameyo's HUNGUP definition exactly
    CASE WHEN COALESCE(USER_TALK_TIME, '00:00:00') = '00:00:00' THEN 1 ELSE 0 END AS is_missed,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(USER_TALK_TIME))    AS talk_sec,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(ACW_DURATION))      AS acw_sec,
    DATEDIFF('second', '00:00:00'::TIME, TRY_TO_TIME(IVR_TIME))          AS queue_wait_sec,
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
  SUM(is_missed)                                       AS missed_calls,
  AVG(CASE WHEN is_missed = 0
      THEN COALESCE(talk_sec, 0) + COALESCE(acw_sec, 0)
      END)                                             AS avg_aht_sec,
  COUNT(DISTINCT USER_ID)                              AS agents_logged,
  AVG(COALESCE(queue_wait_sec, 0))                     AS avg_queue_wait_sec
FROM base
WHERE call_date >= '2026-02-01'
GROUP BY 1
ORDER BY 1
"""

data_dir = Path(os.environ.get("DATA_DIR",
            str(Path(__file__).resolve().parent.parent / "data")))

SQL_HOURLY = """
SELECT
    CALL_TIME::DATE                                                   AS call_date,
    HOUR(CALL_TIME)                                                   AS call_hour,
    COALESCE(NULLIF(TRIM(DISPOSITION_CLASS), ''), 'Missed')          AS disposition_class,
    COALESCE(NULLIF(TRIM(DISPOSITION_CODE),  ''), 'Missed')          AS disposition_code,
    COUNT(*)                                                          AS call_count
FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
WHERE CALL_TYPE = 'inbound.call.dial'
  AND (
    (CALL_TIME::DATE >= '2026-04-01' AND QUEUE_NAME = 'sales_queue')
    OR
    (CALL_TIME::DATE < '2026-04-01' AND QUEUE_NAME IN ('sales_queue', 'booking_queue'))
  )
  AND CALL_TIME::DATE >= DATEADD('day', -8, CURRENT_DATE())
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4
"""

SQL_CSAT = """
SELECT
    TO_DATE(CALL_DATE_AND_TIME, 'MM/DD/YY HH12:MI AM')                   AS call_date,
    COUNT(*)                                                               AS surveys_sent,
    SUM(CASE WHEN TRY_TO_NUMBER(FEEDBACK_INPUT) IS NOT NULL THEN 1 ELSE 0 END)  AS numeric_responses,
    SUM(CASE WHEN TRY_TO_NUMBER(FEEDBACK_INPUT) IN (4,5)   THEN 1 ELSE 0 END)   AS satisfied,
    ROUND(DIV0(
        SUM(CASE WHEN TRY_TO_NUMBER(FEEDBACK_INPUT) IN (4,5) THEN 1 ELSE 0 END),
        SUM(CASE WHEN TRY_TO_NUMBER(FEEDBACK_INPUT) IS NOT NULL THEN 1 ELSE 0 END)
    ) * 100, 1)                                                           AS csat_pct
FROM PROD_DB.PUBLIC.AMEYO_CSAT_REPORT_TABLE
WHERE SOURCE_CAMPAIGN = 'customerSupportInbound'
  AND QUEUE_NAME      = 'sales_queue'
  AND TO_DATE(CALL_DATE_AND_TIME, 'MM/DD/YY HH12:MI AM') >= '2026-02-01'
GROUP BY 1
ORDER BY 1
"""

print("Pulling disposition counts (Sales & Status Queue)...")
run_query(SQL_COUNTS, data_dir / "sales_daily.csv")

print("Pulling daily metrics (Sales & Status Queue)...")
run_query(SQL_METRICS, data_dir / "sales_metrics.csv")

print("Pulling hourly data (Sales & Status Queue — last 8 days)...")
run_query(SQL_HOURLY, data_dir / "sales_hourly.csv")

print("Pulling CSAT data (customerSupportInbound -> sales_queue)...")
run_query(SQL_CSAT, data_dir / "sales_csat.csv")

# ---- Query 5: Sales queue caller phone numbers (for B2I conversion matching) ----
SQL_PHONES = """
SELECT
    CALL_TIME::DATE  AS call_date,
    REGEXP_REPLACE(COALESCE(TRIM(PHONE), ''), '[^0-9]', '') AS phone_clean
FROM PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT
WHERE CALL_TYPE = 'inbound.call.dial'
  AND (
    (CALL_TIME::DATE >= '2026-04-01' AND QUEUE_NAME = 'sales_queue')
    OR
    (CALL_TIME::DATE < '2026-04-01' AND QUEUE_NAME IN ('sales_queue', 'booking_queue'))
  )
  AND CALL_TIME::DATE >= '2026-05-01'
  AND PHONE IS NOT NULL
  AND TRIM(PHONE) != ''
GROUP BY 1, 2
ORDER BY 1, 2
"""

print("Pulling sales queue phone numbers (for B2I conversion)...")
run_query(SQL_PHONES, data_dir / "sales_phones.csv")

# ---- Download B2I bookings from Google Sheet (B2I_Agg tab) ----
B2I_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1FQke_07bIyI46LyirmUGRo6AKWBwXb-mJHmjFsBRT5k"
    "/export?format=csv&gid=411960039"
)

def download_b2i(out_path):
    req = urllib.request.Request(B2I_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        csv_bytes = r.read()
    # Guard: if response looks like an error page, skip
    text = csv_bytes.decode("utf-8", errors="replace").strip()
    if not text or text.startswith("<!"):
        print("  WARNING: B2I download returned unexpected content — skipping.")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(csv_bytes)
    with out_path.open("r", encoding="utf-8") as f:
        n = sum(1 for _ in f) - 1
    print(f"  Rows written: {n}")
    print(f"  Wrote {out_path}")

print("Downloading B2I bookings (Google Sheet -> B2I_Agg)...")
download_b2i(data_dir / "b2i_bookings.csv")
