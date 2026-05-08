"""
Generate a single-file HTML dashboard from data/ameyo_daily.csv.

Output: output/web/index.html  (plus the dashboard is self-contained —
CSS and Chart.js from CDN, no build step).

Served with:  python -m http.server 8765 --directory output/web

Mirrors the Excel dashboard exactly:
  - One tab per month, latest day on the left
  - MTD Avg column bold
  - Class rows pink + bold, Code rows pink indented
  - Spike logic identical:
        daily cell  baseline = row MTD Avg
        MTD cell    baseline = row Last-Month Avg
        upward only; value>=100 -> 15% ; value<100 -> 25%
        spikes show "<value> (+NN%) ↑"
  - Month filter via tabs
  - "MoM Comparison" tab with a Chart.js line chart
"""
import csv
import html
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import os as _os
_BASE = Path(__file__).resolve().parent.parent
SRC = Path(_os.environ.get("DATA_DIR", str(_BASE / "data"))) / "ameyo_daily.csv"
SRC_METRICS = Path(_os.environ.get("DATA_DIR", str(_BASE / "data"))) / "ameyo_metrics.csv"
OUT_DIR = _BASE / "output" / "web"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = OUT_DIR / "index.html"

# ---------------------------------------------------------------- load

rows = list(csv.DictReader(SRC.open()))

# Load daily metrics (AHT, missed calls)
metrics_by_date = {}  # date -> {total, missed, aht_sec}
if SRC_METRICS.exists():
    for mr in csv.DictReader(SRC_METRICS.open()):
        d = date.fromisoformat(mr["CALL_DATE"])
        metrics_by_date[d] = {
            "total":  int(mr["TOTAL_CALLS"]),
            "missed": int(mr["MISSED_CALLS"]),
            "aht":    float(mr["AVG_AHT_SEC"]),
            "agents": int(mr.get("AGENTS_LOGGED", 0)),
        }

CLASS_MERGE_EXACT = {
    "Sales-App Issues":                 "Sales Queue",
    "Sales-Next Steps":                 "Sales Queue",
    "Sales-Next Steps - Old Contruct":  "Sales Queue",
    "Sales-Pause":                      "Sales Queue",
    "Sales-System Understanding":       "Sales Queue",
    "Sales-System Understanding - Old": "Sales Queue",
}
def display_class(src_cls: str) -> str:
    if src_cls in CLASS_MERGE_EXACT:
        return CLASS_MERGE_EXACT[src_cls]
    if src_cls.startswith("Sales-") or src_cls.startswith("Sales "):
        return "Sales Queue"
    return src_cls

COLLAPSED_CLASSES  = {"Sales Queue", "Booking Queue", "(Unclassified)",
                      "PayG - Clarification Provided",
                      "user.forced.logged.off",
                      "user.transferred.to.campaign"}
COLLAPSED_SENTINEL = "__ALL__"

counts = defaultdict(lambda: defaultdict(int))
all_dates = set()
for r in rows:
    d = date.fromisoformat(r["CALL_DATE"])
    src_cls = r["DISPOSITION_CLASS"]
    cls = display_class(src_cls)
    key_code = COLLAPSED_SENTINEL if cls in COLLAPSED_CLASSES else r["DISPOSITION_CODE"]
    counts[(cls, key_code)][d] += int(r["CALL_COUNT"])
    all_dates.add(d)

by_month = defaultdict(set)
for d in all_dates:
    by_month[(d.year, d.month)].add(d)
months_sorted = sorted(by_month.keys())

# Only show tabs for March 2026 onwards; earlier months are used only
# as "previous month" data for averages.
DISPLAY_FROM = (2026, 3)  # (year, month)
display_months = [ym for ym in months_sorted if ym >= DISPLAY_FROM]

PINNED_TOP    = ["Internet Related Issues", "Payment Related Issues", "Others"]
MIDDLE_ORDER  = [
    "Change Request",
    "Partner Misbehavior",
    "Refund",
    "Remove Connection - Talk to Customer",
    "Shifting Request",
    "Wiom Net",
    "Incomplete Call",
    "campaign.system.disposition",
    "user.forced.logged.off",
    "user.transferred.to.campaign",
]
PINNED_BOTTOM = ["Sales Queue", "Booking Queue", "(Unclassified)"]

cls_codes = defaultdict(set)
for cls, code in counts.keys():
    cls_codes[cls].add(code)

_known = set(PINNED_TOP) | set(MIDDLE_ORDER) | set(PINNED_BOTTOM)
_extra = sorted(c for c in cls_codes if c not in _known)
_order = [c for c in PINNED_TOP     if c in cls_codes] + \
         [c for c in MIDDLE_ORDER   if c in cls_codes] + _extra + \
         [c for c in PINNED_BOTTOM  if c in cls_codes]

taxonomy = []
for cls in _order:
    if cls in COLLAPSED_CLASSES:
        taxonomy.append((cls, []))
    else:
        taxonomy.append((cls, sorted(c for c in cls_codes[cls] if c != COLLAPSED_SENTINEL)))

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def fmt_month(ym): return f"{MONTH_NAMES[ym[1]-1]} {ym[0]}"

def avg(vals):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0

def threshold_for(value):
    v = abs(value)
    if v >= 100:
        return 0.15
    if v >= 20:
        return 0.25
    return 1.00

def spike(value, baseline):
    if baseline is None or baseline <= 0 or value is None:
        return (False, 0)
    pct = (value - baseline) / baseline
    if pct <= 0:
        return (False, 0)
    return (pct >= threshold_for(value), round(pct * 100))

def cell_html(value, baseline, is_mtd=False, is_prev=False,
              drill=None):
    """
    Numeric cell.  `drill` is a dict describing the row/column scope so the
    JS dblclick handler can ask the server for the underlying rows.
        {"cls": "...", "code": "..." | None,
         "scope": "day" | "mtd" | "prev",
         "date":  "YYYY-MM-DD" (for "day"),
         "ym":    "YYYY-MM"    (for mtd/prev)}
    """
    spiked, pct = spike(value, baseline)
    rounded = int(round(value or 0))
    classes = ["num"]
    if is_mtd:
        classes.append("mtd")
    if is_prev:
        classes.append("prev")
    if is_mtd or is_prev:
        classes.append("avgcol")

    if spiked:
        inner = f'{rounded}<span class="spike">(+{pct}%) &uarr;</span>'
    else:
        inner = str(rounded)

    attrs = ""
    if drill:
        attrs = (
            f' data-cls="{html.escape(drill["cls"])}"'
            f' data-code="{html.escape(drill.get("code") or "")}"'
            f' data-scope="{drill["scope"]}"'
            f' data-date="{drill.get("date","")}"'
            f' data-ym="{drill.get("ym","")}"'
            ' title="Double-click for raw data"'
        )
    return f'<td class="{" ".join(classes)}"{attrs}>{inner}</td>'

def fmt_aht(seconds):
    """Format AHT seconds as M:SS."""
    if seconds is None or seconds <= 0:
        return "0:00"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"

def fmt_pct(val):
    """Format as percentage with 1 decimal."""
    if val is None:
        return "0.0%"
    return f"{val:.1f}%"

# ---------------------------------------------------------------- build metrics table

def metrics_table(ym):
    """Build a small summary table: Call Volume, Missed Call, AHT per day."""
    TODAY = date.today()
    days_desc = sorted(by_month[ym], reverse=True)
    prev_ym = (ym[0], ym[1] - 1) if ym[1] > 1 else (ym[0] - 1, 12)
    prev_days = sorted(by_month.get(prev_ym, []))
    prev_label = f"{fmt_month(prev_ym)} Avg" if prev_days else "Prev Month Avg"
    cur_avg_label = f"{fmt_month(ym)} Avg (MTD)"

    headers = ["Metrics", cur_avg_label, prev_label] + [d.strftime("%d-%b") for d in days_desc]
    thead = "<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>"

    # Gather per-day values (all days including today for daily columns)
    vol_day   = {d: metrics_by_date.get(d, {}).get("total", 0) for d in days_desc}
    miss_day  = {d: metrics_by_date.get(d, {}).get("missed", 0) for d in days_desc}
    aht_day   = {d: metrics_by_date.get(d, {}).get("aht", 0) for d in days_desc}
    vol_prev  = {d: metrics_by_date.get(d, {}).get("total", 0) for d in prev_days}
    miss_prev = {d: metrics_by_date.get(d, {}).get("missed", 0) for d in prev_days}
    aht_prev  = {d: metrics_by_date.get(d, {}).get("aht", 0) for d in prev_days}

    # MTD avg: exclude today (partial day) — only complete days count
    complete_days = [d for d in days_desc if d < TODAY]
    vol_complete  = {d: vol_day[d]  for d in complete_days}
    miss_complete = {d: miss_day[d] for d in complete_days}
    aht_complete  = {d: aht_day[d]  for d in complete_days}

    vol_mtd  = avg(vol_complete.values())  if vol_complete  else 0
    vol_pavg = avg(vol_prev.values())      if vol_prev      else 0
    miss_mtd  = avg(miss_complete.values()) if miss_complete else 0
    miss_pavg = avg(miss_prev.values())     if miss_prev     else 0
    aht_mtd  = avg(aht_complete.values())  if aht_complete  else 0
    aht_pavg = avg(aht_prev.values())      if aht_prev      else 0

    # Missed call % per day (all days for daily columns)
    misspct_day = {}
    for d in days_desc:
        t = vol_day.get(d, 0)
        m = miss_day.get(d, 0)
        misspct_day[d] = (m / t * 100) if t else 0
    misspct_prev = {}
    for d in prev_days:
        t = vol_prev.get(d, 0)
        m = miss_prev.get(d, 0)
        misspct_prev[d] = (m / t * 100) if t else 0
    # Weighted missed % MTD: complete days only
    _tot_calls_mtd = sum(vol_complete.values())
    _tot_miss_mtd  = sum(miss_complete.values())
    misspct_mtd  = (_tot_miss_mtd / _tot_calls_mtd * 100) if _tot_calls_mtd else 0
    _tot_calls_prev = sum(vol_prev.values())
    _tot_miss_prev  = sum(miss_prev.values())
    misspct_pavg = (_tot_miss_prev / _tot_calls_prev * 100) if _tot_calls_prev else 0

    rows_html = []

    # Row 1: Call Volume
    cells = ['<td class="disp disp-class">Call Volume</td>']
    cells.append(f'<td class="num mtd avgcol">{int(round(vol_mtd))}</td>')
    cells.append(f'<td class="num prev avgcol">{int(round(vol_pavg))}</td>')
    for d in days_desc:
        cells.append(f'<td class="num">{vol_day.get(d, 0)}</td>')
    rows_html.append(f'<tr class="row-class">{"".join(cells)}</tr>')

    # Row 2: Missed Call %
    cells = ['<td class="disp disp-class">Missed Call %</td>']
    cells.append(f'<td class="num mtd avgcol">{fmt_pct(misspct_mtd)}</td>')
    cells.append(f'<td class="num prev avgcol">{fmt_pct(misspct_pavg)}</td>')
    for d in days_desc:
        cells.append(f'<td class="num">{fmt_pct(misspct_day.get(d, 0))}</td>')
    rows_html.append(f'<tr class="row-class">{"".join(cells)}</tr>')

    # Row 3: AHT (M:SS)
    cells = ['<td class="disp disp-class">AHT (Avg Handle Time)</td>']
    cells.append(f'<td class="num mtd avgcol">{fmt_aht(aht_mtd)}</td>')
    cells.append(f'<td class="num prev avgcol">{fmt_aht(aht_pavg)}</td>')
    for d in days_desc:
        cells.append(f'<td class="num">{fmt_aht(aht_day.get(d, 0))}</td>')
    rows_html.append(f'<tr class="row-class">{"".join(cells)}</tr>')

    # Row 4: Agents Logged (unique USER_IDs that took calls)
    agents_day  = {d: metrics_by_date.get(d, {}).get("agents", 0) for d in days_desc}
    agents_prev = {d: metrics_by_date.get(d, {}).get("agents", 0) for d in prev_days}
    agents_complete = {d: agents_day[d] for d in complete_days}
    agents_mtd  = avg(agents_complete.values()) if agents_complete else 0
    agents_pavg = avg(agents_prev.values())     if agents_prev     else 0
    cells = ['<td class="disp disp-class">Agents Logged</td>']
    cells.append(f'<td class="num mtd avgcol">{int(round(agents_mtd))}</td>')
    cells.append(f'<td class="num prev avgcol">{int(round(agents_pavg))}</td>')
    for d in days_desc:
        cells.append(f'<td class="num">{agents_day.get(d, 0)}</td>')
    rows_html.append(f'<tr class="row-class">{"".join(cells)}</tr>')

    return f'<table class="dash metrics-dash"><thead>{thead}</thead><tbody>{"".join(rows_html)}</tbody></table>'

# ---------------------------------------------------------------- build month tables

def month_table(ym):
    TODAY     = date.today()
    days_desc = sorted(by_month[ym], reverse=True)
    # Complete days = all days before today (exclude partial current day from avg)
    complete_days = [d for d in days_desc if d < TODAY]
    prev_ym = (ym[0], ym[1] - 1) if ym[1] > 1 else (ym[0] - 1, 12)
    prev_days = sorted(by_month.get(prev_ym, []))
    prev_label = f"{fmt_month(prev_ym)} Avg" if prev_days else "Prev Month Avg"

    ym_str      = f"{ym[0]:04d}-{ym[1]:02d}"
    prev_ym_str = f"{prev_ym[0]:04d}-{prev_ym[1]:02d}"
    cur_avg_label = f"{fmt_month(ym)} Avg (MTD)"

    headers = ["Category", cur_avg_label, prev_label] + [d.strftime("%d-%b") for d in days_desc]
    thead = "<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>"

    body_rows = []
    total_day = defaultdict(int)
    total_prev_day = defaultdict(int)

    def mk_drill(cls, code, scope, d=None):
        return {"cls": cls, "code": code, "scope": scope,
                "date": d.isoformat() if d else "",
                "ym": ym_str if scope != "prev" else prev_ym_str}

    for cls, codes in taxonomy:
        class_day      = {d: 0 for d in days_desc}
        class_prev_day = {d: 0 for d in prev_days}
        for (k_cls, _k_code), dd in counts.items():
            if k_cls != cls:
                continue
            for d, n in dd.items():
                if d in class_day:
                    class_day[d] += n
                if d in class_prev_day:
                    class_prev_day[d] += n
                ym_of_d = (d.year, d.month)
                if ym_of_d == ym:
                    total_day[d] += n
                if ym_of_d == prev_ym:
                    total_prev_day[d] += n

        # MTD avg uses complete days only (exclude today's partial data)
        cls_mtd  = avg(class_day[d] for d in complete_days) if complete_days else 0
        cls_prev = avg(class_prev_day.values())

        cls_id = html.escape(cls.replace(" ", "_").replace("(", "").replace(")", ""))
        has_codes = len(codes) > 0
        toggle_attr = f' data-toggle="{cls_id}"' if has_codes else ''
        toggle_icon = '<span class="toggle-icon">&#9660;</span> ' if has_codes else ''
        cells = [f'<td class="disp disp-class"{toggle_attr}>{toggle_icon}{html.escape(cls)}</td>']
        cells.append(cell_html(cls_mtd, cls_prev, is_mtd=True,
                               drill=mk_drill(cls, None, "mtd")))
        cells.append(cell_html(cls_prev, None, is_prev=True,
                               drill=mk_drill(cls, None, "prev")))
        for d in days_desc:
            cells.append(cell_html(class_day.get(d, 0), cls_mtd,
                                   drill=mk_drill(cls, None, "day", d)))
        body_rows.append(f'<tr class="row-class">{"".join(cells)}</tr>')

        # Sort codes descending by MTD Avg (complete days) for cleaner view
        code_mtds = []
        for code in codes:
            dv = {d: counts[(cls, code)].get(d, 0) for d in complete_days}
            code_mtds.append((code, avg(dv.values()) if dv else 0))
        code_mtds.sort(key=lambda x: x[1], reverse=True)

        for code, _ in code_mtds:
            day_vals  = {d: counts[(cls, code)].get(d, 0) for d in days_desc}
            prev_vals = {d: counts[(cls, code)].get(d, 0) for d in prev_days}
            # MTD avg: complete days only
            mtd  = avg(day_vals[d] for d in complete_days) if complete_days else 0
            prev = avg(prev_vals.values())

            cells = [f'<td class="disp disp-code">{html.escape(code)}</td>']
            cells.append(cell_html(mtd, prev, is_mtd=True,
                                   drill=mk_drill(cls, code, "mtd")))
            cells.append(cell_html(prev, None, is_prev=True,
                                   drill=mk_drill(cls, code, "prev")))
            for d in days_desc:
                cells.append(cell_html(day_vals.get(d, 0), mtd,
                                       drill=mk_drill(cls, code, "day", d)))
            body_rows.append(f'<tr class="row-code code-of-{cls_id}">{"".join(cells)}</tr>')

    # TOTAL row — complete days only for MTD avg
    tot_mtd  = avg(total_day[d] for d in complete_days) if complete_days else 0
    tot_prev = avg(total_prev_day.values())
    tot_cells = ['<td class="disp total-label">TOTAL</td>']
    tot_cells.append(cell_html(tot_mtd, tot_prev, is_mtd=True))
    tot_cells.append(cell_html(tot_prev, None, is_prev=True))
    for d in days_desc:
        tot_cells.append(cell_html(total_day.get(d, 0), tot_mtd))
    body_rows.append(f'<tr class="row-total">{"".join(tot_cells)}</tr>')

    disp_table = f'<table class="dash"><thead>{thead}</thead><tbody>{"".join(body_rows)}</tbody></table>'
    mtable = metrics_table(ym)
    return f'{mtable}<div style="margin-top:16px"></div>{disp_table}'


# ---------------------------------------------------------------- MoM data

class_totals_by_month = defaultdict(lambda: defaultdict(int))
for (cls, code), dd in counts.items():
    for d, n in dd.items():
        class_totals_by_month[cls][(d.year, d.month)] += n

mom_labels = [fmt_month(m) for m in months_sorted]
mom_classes = [cls for cls, _ in taxonomy]
mom_datasets = []
for ym in months_sorted:
    row = []
    for cls in mom_classes:
        total = class_totals_by_month[cls].get(ym, 0)
        nd = len(by_month[ym])
        row.append(int(round(total / nd)) if nd else 0)
    mom_datasets.append({"label": fmt_month(ym), "data": row})

# MoM table
mom_rows = []
for i, cls in enumerate(mom_classes):
    tds = [f'<td class="disp disp-class">{html.escape(cls)}</td>']
    for j, ym in enumerate(months_sorted):
        v = mom_datasets[j]["data"][i]
        tds.append(f'<td class="num">{v}</td>')
    mom_rows.append(f'<tr class="row-class">{"".join(tds)}</tr>')
# total
tot_tds = ['<td class="disp total-label">TOTAL (all classes)</td>']
for j, ym in enumerate(months_sorted):
    s = sum(mom_datasets[j]["data"])
    tot_tds.append(f'<td class="num mtd">{int(round(s))}</td>')
mom_rows.append(f'<tr class="row-total">{"".join(tot_tds)}</tr>')
mom_head = "<tr>" + "<th>Disposition Class</th>" + "".join(
    f"<th>{fmt_month(m)} Avg</th>" for m in months_sorted
) + "</tr>"
mom_table = f'<table class="dash"><thead>{mom_head}</thead><tbody>{"".join(mom_rows)}</tbody></table>'

# ---- Top 10 Disposition Codes MoM chart data ----
code_totals_by_month = defaultdict(lambda: defaultdict(int))
code_grand_total = defaultdict(int)
for (cls, code), dd in counts.items():
    if cls in COLLAPSED_CLASSES:
        continue  # skip collapsed classes (no individual codes)
    for d, n in dd.items():
        ym_key = (d.year, d.month)
        code_totals_by_month[code][ym_key] += n
        code_grand_total[code] += n

# Pick top 15 codes by grand total volume
top10_codes = sorted(code_grand_total.keys(), key=lambda c: code_grand_total[c], reverse=True)[:15]

top10_datasets = []
for ym in months_sorted:
    row = []
    for code in top10_codes:
        total = code_totals_by_month[code].get(ym, 0)
        nd = len(by_month[ym])
        row.append(int(round(total / nd)) if nd else 0)
    top10_datasets.append({"label": fmt_month(ym), "data": row})

# ---- NEW: flipped-axis chart data (x=months, one series per class/code) ----
_PALETTE = ['#E91E8C','#1E88E5','#43A047','#FB8C00','#8E24AA',
            '#00897B','#D81B60','#3949AB','#F4511E','#6D4C41',
            '#00ACC1','#7CB342','#FFB300','#5C6BC0','#EC407A']

mom_month_labels = [fmt_month(m) for m in months_sorted]

# {cls: [avg_per_month]}  — used by JS for the category chart
mom_cat_series = {}
for i, cls in enumerate(mom_classes):
    series = []
    for ym in months_sorted:
        total = class_totals_by_month[cls].get(ym, 0)
        nd = len(by_month[ym])
        series.append(round(total / nd, 1) if nd else 0)
    mom_cat_series[cls] = series

# pre-assign palette so colours stay stable when filtering
mom_cat_colors = {cls: _PALETTE[i % len(_PALETTE)] for i, cls in enumerate(mom_classes)}

# {code: [avg_per_month]} for the sub-category chart
top15_code_series = {}
for i, code in enumerate(top10_codes):
    series = []
    for ym in months_sorted:
        total = code_totals_by_month[code].get(ym, 0)
        nd = len(by_month[ym])
        series.append(round(total / nd, 1) if nd else 0)
    top15_code_series[code] = series

top15_code_colors = {code: _PALETTE[i % len(_PALETTE)] for i, code in enumerate(top10_codes)}

# map each code → its parent class (so the filter can scope chart 2)
code_class_map_js = {}
for cls, codes in taxonomy:
    for code in codes:
        code_class_map_js[code] = cls

# build <option> elements for the filter dropdown
chart_filter_options = "\n      ".join(
    f'<option value="{html.escape(cls)}">{html.escape(cls)}</option>'
    for cls in mom_classes
)

# Top 10 codes table
top10_rows = []
for i, code in enumerate(top10_codes):
    tds = [f'<td class="disp disp-code" style="padding-left:10px">{html.escape(code)}</td>']
    for j, ym in enumerate(months_sorted):
        v = top10_datasets[j]["data"][i]
        tds.append(f'<td class="num">{v}</td>')
    top10_rows.append(f'<tr class="row-code">{"".join(tds)}</tr>')
top10_head = "<tr>" + "<th>Sub-category Issue</th>" + "".join(
    f"<th>{fmt_month(m)} Avg</th>" for m in months_sorted
) + "</tr>"
top10_table = f'<table class="dash"><thead>{top10_head}</thead><tbody>{"".join(top10_rows)}</tbody></table>'

# ---------------------------------------------------------------- tabs

tabs_buttons = []
tabs_panels = []
# month tabs (latest first, only display months)
for ym in reversed(display_months):
    tid = f"tab-{ym[0]}-{ym[1]:02d}"
    tabs_buttons.append(f'<button class="tab-btn" data-target="{tid}">{fmt_month(ym)}</button>')
    tabs_panels.append(f'<section id="{tid}" class="tab-panel">{month_table(ym)}</section>')
# MoM tab
tabs_buttons.append('<button class="tab-btn" data-target="tab-mom">MoM Comparison</button>')
mom_panel = f'''
<section id="tab-mom" class="tab-panel">

  <div class="chart-controls">
    <label class="filter-label">&#9660; Filter charts by Disposition Class:</label>
    <select id="chartClassFilter" onchange="applyChartFilter(this.value)">
      <option value="">— All Categories —</option>
      {chart_filter_options}
    </select>
    <button class="filter-clear-btn" onclick="document.getElementById('chartClassFilter').value='';applyChartFilter('')">&#10005; Clear</button>
    <span id="chartFilterNote" class="filter-note" style="display:none"></span>
  </div>

  <div class="chart-wrap" style="position:relative; height:500px;">
    <canvas id="momChart"></canvas>
  </div>
  {mom_table}

  <div style="margin-top:28px"></div>

  <div class="chart-wrap" style="position:relative; height:500px;">
    <canvas id="top10Chart"></canvas>
  </div>
  {top10_table}

</section>
'''
tabs_panels.append(mom_panel)

# Per-month totals, keyed by tab id ("tab-YYYY-MM") so JS can swap the
# "Total Inbound Calls" stat as the user changes tabs.
per_tab_totals = {}
for ym in display_months:
    tot = 0
    for (_cls, _code), dd in counts.items():
        for d, n in dd.items():
            if (d.year, d.month) == ym:
                tot += n
    per_tab_totals[f"tab-{ym[0]}-{ym[1]:02d}"] = tot
per_tab_totals["tab-mom"] = sum(per_tab_totals.values())

# "Last Refreshed" = mtime of the source CSV
try:
    refreshed_at = datetime.fromtimestamp(SRC.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
except Exception:
    refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M")

# Also expose per-month disposition-code counts for the stat
codes_per_tab = {}
for ym in display_months:
    seen = set()
    for (cls, code), dd in counts.items():
        if any((d.year, d.month) == ym for d in dd):
            if cls not in COLLAPSED_CLASSES:
                seen.add((cls, code))
    # collapsed classes count as 0 codes in display
    codes_per_tab[f"tab-{ym[0]}-{ym[1]:02d}"] = len(seen)
codes_per_tab["tab-mom"] = sum(len(c) for _, c in taxonomy)

# Per-tab Missed Call % MTD and AHT MTD
# Use complete days only (exclude today's partial data from averages)
_TODAY = date.today()
per_tab_misspct = {}
per_tab_aht = {}
per_tab_totals_complete = {}  # for top banner total (complete days)
for ym in display_months:
    tab_id = f"tab-{ym[0]}-{ym[1]:02d}"
    ym_dates = [d for d in metrics_by_date if (d.year, d.month) == ym]
    complete = [d for d in ym_dates if d < _TODAY]
    use_dates = complete if complete else ym_dates  # fallback if all dates are today
    if use_dates:
        total_calls = sum(metrics_by_date[d]["total"] for d in use_dates)
        total_missed = sum(metrics_by_date[d]["missed"] for d in use_dates)
        daily_ahts = [metrics_by_date[d]["aht"] for d in use_dates]
        weighted_pct = (total_missed / total_calls * 100) if total_calls else 0
        per_tab_misspct[tab_id] = round(weighted_pct, 1)
        per_tab_aht[tab_id] = fmt_aht(avg(daily_ahts))
    else:
        per_tab_misspct[tab_id] = 0
        per_tab_aht[tab_id] = "0:00"
# MoM tab: average across all months
all_pcts = [v for v in per_tab_misspct.values()]
all_ahts_sec = []
for ym in months_sorted:
    ym_dates = [d for d in metrics_by_date if (d.year, d.month) == ym]
    for d in ym_dates:
        all_ahts_sec.append(metrics_by_date[d]["aht"])
per_tab_misspct["tab-mom"] = round(avg(all_pcts), 1) if all_pcts else 0
per_tab_aht["tab-mom"] = fmt_aht(avg(all_ahts_sec)) if all_ahts_sec else "0:00"

# ---------------------------------------------------------------- highlights
# Per-tab highlights: for each display month, find top spikes on the latest day.

def compute_highlights(ym):
    """Return list of spike dicts: MTD avg vs previous month avg."""
    hl = []
    if ym not in by_month:
        return hl
    prev_ym = (ym[0], ym[1] - 1) if ym[1] > 1 else (ym[0] - 1, 12)
    prev_days_set = by_month.get(prev_ym, set())
    for cls, codes in taxonomy:
        # Current month daily values for this class
        class_cur_vals = {}
        class_prev_vals = {}
        for (k_cls, _k_code), dd in counts.items():
            if k_cls != cls:
                continue
            for d, n in dd.items():
                if (d.year, d.month) == ym:
                    class_cur_vals[d] = class_cur_vals.get(d, 0) + n
                if (d.year, d.month) == prev_ym:
                    class_prev_vals[d] = class_prev_vals.get(d, 0) + n
        cls_mtd = avg(class_cur_vals.values()) if class_cur_vals else 0
        cls_prev = avg(class_prev_vals.values()) if class_prev_vals else 0
        spiked, pct = spike(cls_mtd, cls_prev)
        if spiked:
            hl.append({"name": cls, "val": int(round(cls_mtd)), "pct": pct, "is_class": True})
        for code in codes:
            code_cur = {d: counts[(cls, code)].get(d, 0) for d in by_month[ym]}
            code_prev = {d: counts[(cls, code)].get(d, 0) for d in prev_days_set}
            mtd_avg = avg(code_cur.values()) if code_cur else 0
            prev_avg = avg(code_prev.values()) if code_prev else 0
            s2, p2 = spike(mtd_avg, prev_avg)
            if s2:
                hl.append({"name": code, "val": int(round(mtd_avg)), "pct": p2, "is_class": False})
    hl.sort(key=lambda x: x["pct"], reverse=True)
    return hl[:8]

def build_hl_html(ym, tab_id):
    """Build a highlights div for a given month, with data-tab attribute."""
    hl = compute_highlights(ym)
    if not hl:
        return ""
    prev_ym = (ym[0], ym[1] - 1) if ym[1] > 1 else (ym[0] - 1, 12)
    chips = []
    for h in hl:
        weight = "700" if h["is_class"] else "400"
        chips.append(
            f'<span class="hl-chip" style="font-weight:{weight}">'
            f'{html.escape(h["name"])} '
            f'<span class="hl-val">{h["val"]}</span>'
            f'<span class="hl-pct">(+{h["pct"]}%) &uarr;</span>'
            f'</span>'
        )
    return (
        f'<div class="highlights hl-tab" data-tab="{tab_id}">'
        f'<div class="hl-title">Top spikes — {fmt_month(ym)} avg vs {fmt_month(prev_ym)} avg</div>'
        f'<div class="hl-chips">{"".join(chips)}</div>'
        f'</div>'
    )

highlights_sections = []
for ym in display_months:
    tab_id = f"tab-{ym[0]}-{ym[1]:02d}"
    h = build_hl_html(ym, tab_id)
    if h:
        highlights_sections.append(h)
highlights_html = "\n".join(highlights_sections)

# ---------------------------------------------------------------- page

PAGE = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Wiom — Daily Inbound Call Dashboard (Service Queue)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {{
  --pink:        #E91E8C;
  --pink-light:  #FCE4EC;
  --pink-hover:  #F8BBD0;
  --red:         #C62828;
  --grey:        #D0D0D0;
  --grey-soft:   #F5F5F5;
  --text:        #000;
  --muted:       #666;
  --avg-bg:         #E3F2FD;
  --avg-bg-class:   #BBDEFB;
  --avg-bg-total:   #90CAF9;
  --header-grad:  linear-gradient(135deg, #880E4F 0%, #E91E8C 100%);
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: #FAFAFA; color: var(--text);
  font-family: -apple-system, "Segoe UI", Calibri, Arial, sans-serif; font-size: 13px; }}

/* ---- header ---- */
header.main-header {{ padding: 24px 32px 16px; background: #fff;
  border-bottom: 1px solid var(--grey); }}
header.main-header h1 {{ margin: 0 0 4px; color: var(--pink); font-size: 24px; font-weight: 700;
  letter-spacing: -.3px; }}
header.main-header .sub {{ color: var(--muted); font-size: 11.5px; }}
header.main-header .stats {{ margin-top: 14px; display: flex; gap: 14px; flex-wrap: wrap; }}
header.main-header .stats .stat-card {{
  background: #fff; padding: 10px 18px; border-radius: 10px; min-width: 120px;
  border: 1px solid var(--pink-light);
  transition: transform .15s, box-shadow .15s; }}
header.main-header .stats .stat-card:hover {{ transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(233,30,140,.12); }}
header.main-header .stats .k {{ color: var(--muted); font-size: 10px;
  text-transform: uppercase; letter-spacing: .6px; margin-bottom: 2px; }}
header.main-header .stats .v {{ font-weight: 700; font-size: 18px; color: var(--pink); }}

/* ---- highlights bar ---- */
.highlights {{ background: #FFF3E0; padding: 10px 32px; border-bottom: 1px solid #FFE0B2;
  display: none; align-items: center; gap: 12px; flex-wrap: wrap; }}
.highlights.active {{ display: flex; }}
.hl-title {{ font-weight: 700; font-size: 11.5px; color: #E65100; white-space: nowrap; }}
.hl-chips {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.hl-chip {{ background: #fff; border: 1px solid #FFCC80; border-radius: 14px;
  padding: 3px 12px; font-size: 11.5px; color: var(--text); white-space: nowrap;
  transition: box-shadow .15s; }}
.hl-chip:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,.1); }}
.hl-val {{ font-weight: 700; margin-right: 3px; }}
.hl-pct {{ color: var(--red); font-size: 10px; font-weight: 600; }}

/* ---- tabs ---- */
nav.tabs {{ padding: 0 32px; border-bottom: 1px solid var(--grey); background: #fff;
  position: sticky; top: 0; z-index: 25; display: flex; align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
.tab-btn {{ background: none; border: none; padding: 12px 18px; cursor: pointer;
  color: var(--muted); font-weight: 600; font-size: 13px;
  border-bottom: 3px solid transparent; transition: color .15s, border-color .15s; }}
.tab-btn:hover {{ color: var(--pink); }}
.tab-btn.active {{ color: var(--pink); border-bottom-color: var(--pink); }}
.tab-spacer {{ flex: 1; }}
/* search box in tab bar */
.search-box {{ position: relative; margin-left: auto; }}
.search-box input {{ border: 1px solid var(--grey); border-radius: 16px; padding: 6px 14px 6px 32px;
  font-size: 12px; width: 200px; outline: none; transition: border-color .2s, width .2s; }}
.search-box input:focus {{ border-color: var(--pink); width: 260px; }}
.search-box svg {{ position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  width: 14px; height: 14px; fill: var(--muted); pointer-events: none; }}

/* NOTE: do NOT put overflow-x:auto on <main>. */
main {{ padding: 20px 32px 60px; }}
.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; }}

/* ---- dashboard table ---- */
table.dash {{ border-collapse: separate; border-spacing: 0;
  border: 1px solid var(--grey); background: #fff; font-size: 12.5px;
  box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
table.dash th, table.dash td {{
  border-right: 1px solid var(--grey); border-bottom: 1px solid var(--grey);
  padding: 6px 10px; background-clip: padding-box; }}
table.dash thead th {{
  background: var(--grey-soft); color: var(--text);
  font-weight: 700; text-align: center;
  position: sticky; top: 58px; z-index: 20;
  border-top: 1px solid var(--grey);
  box-shadow: 0 2px 4px rgba(0,0,0,.08);
}}
/* first column — sticky left */
table.dash th:first-child, table.dash td:first-child {{
  position: sticky; left: 0; z-index: 15;
  box-shadow: 2px 0 4px rgba(0,0,0,.06);
}}
table.dash thead th:first-child {{ z-index: 30; }}

table.dash td.disp {{ color: var(--pink); text-align: left; white-space: nowrap; min-width: 300px; }}
table.dash td.disp-class {{ font-weight: 700; cursor: pointer; user-select: none; }}
table.dash td.disp-code {{ padding-left: 26px; font-weight: 400; }}
/* toggle icon for collapsible class rows */
.toggle-icon {{ display: inline-block; font-size: 9px; margin-right: 4px;
  transition: transform .2s; color: var(--muted); }}
.toggle-icon.collapsed {{ transform: rotate(-90deg); }}

/* absolute number is ALWAYS black */
table.dash td.num {{ color: var(--text); text-align: center; min-width: 100px;
  cursor: pointer; user-select: none; transition: background .15s; }}
table.dash td.num:hover {{ filter: brightness(.96); }}
table.dash td.mtd {{ font-weight: 700; }}
/* spike span — on its own line below the number for clean alignment */
table.dash td.num .spike {{ color: var(--red); font-weight: 600; white-space: nowrap;
  display: block; font-size: 9px; line-height: 1.2; margin-top: 1px; }}

/* row backgrounds */
table.dash tr.row-class td {{ background: var(--pink-light); }}
table.dash tr.row-class td:first-child {{ background: var(--pink-light); }}
table.dash tr.row-code  td:first-child {{ background: #fff; }}
table.dash tr.row-class:hover td {{ background: var(--pink-hover); }}
table.dash tr.row-class:hover td:first-child {{ background: var(--pink-hover); }}
/* alternating code rows */
table.dash tr.row-code:nth-child(even) td {{ background: #FAFAFA; }}
table.dash tr.row-code:nth-child(even) td:first-child {{ background: #FAFAFA; }}
table.dash tr.row-code:hover  td {{ background: #F0F0F0; }}
table.dash tr.row-code:hover  td:first-child {{ background: #F0F0F0; }}
table.dash tr.row-total td {{ background: #EEE; font-weight: 700; }}
table.dash tr.row-total td:first-child {{ background: #EEE; }}
/* hidden code rows (collapsed) */
table.dash tr.row-code.hidden {{ display: none; }}

/* Avg columns */
table.dash td.num.avgcol              {{ background: var(--avg-bg) !important; }}
table.dash tr.row-class td.num.avgcol {{ background: var(--avg-bg-class) !important; }}
table.dash tr.row-total td.num.avgcol {{ background: var(--avg-bg-total) !important; }}
table.dash thead th:nth-child(2),
table.dash thead th:nth-child(3) {{ background: var(--avg-bg-class); }}

/* search highlight */
table.dash tr.search-miss {{ display: none; }}

/* ---- metrics summary table ---- */
table.metrics-dash {{ margin-bottom: 0; }}
table.metrics-dash td.disp-class {{ background: #E8F5E9 !important; color: #2E7D32; font-weight: 700; }}
table.metrics-dash tr.row-class:hover td {{ background: #C8E6C9 !important; }}
table.metrics-dash thead th {{ background: #E8F5E9 !important; color: #2E7D32; }}
table.metrics-dash thead th:nth-child(2),
table.metrics-dash thead th:nth-child(3) {{ background: #C8E6C9 !important; }}
table.metrics-dash td.num.avgcol {{ background: #C8E6C9 !important; }}

.legend {{ margin: 10px 0 16px; color: var(--muted); font-size: 11.5px; line-height: 1.6;
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
.legend b {{ color: var(--text); }}
.legend .pill {{ background: var(--grey-soft); padding: 3px 10px; border-radius: 10px;
  font-size: 11px; }}

.chart-wrap {{ background: #fff; padding: 16px; border: 1px solid var(--grey);
  border-radius: 8px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}

/* chart filter bar */
.chart-controls {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 14px; padding: 10px 16px; background: #fff;
  border: 1px solid var(--grey); border-radius: 8px; }}
.filter-label {{ font-size: 12px; font-weight: 600; color: var(--muted); white-space: nowrap; }}
.chart-controls select {{ border: 1px solid var(--grey); border-radius: 6px;
  padding: 5px 10px; font-size: 12px; min-width: 220px; outline: none;
  cursor: pointer; transition: border-color .2s; }}
.chart-controls select:focus {{ border-color: var(--pink); }}
.filter-clear-btn {{ background: none; border: 1px solid var(--grey); border-radius: 6px;
  padding: 4px 10px; font-size: 11px; color: var(--muted); cursor: pointer;
  transition: border-color .15s, color .15s; }}
.filter-clear-btn:hover {{ border-color: var(--red); color: var(--red); }}
.filter-note {{ font-size: 11px; color: var(--pink); font-weight: 600; }}
footer {{ color: var(--muted); font-size: 11px; text-align: center; padding: 24px;
  background: #fff; border-top: 1px solid var(--grey); }}

/* raw-data drill-through modal */
.modal-backdrop {{ position: fixed; inset: 0; background: rgba(0,0,0,.5);
  display: none; align-items: flex-start; justify-content: center; z-index: 100;
  padding: 40px 20px; overflow-y: auto; backdrop-filter: blur(2px); }}
.modal-backdrop.open {{ display: flex; }}
.modal {{ background: #fff; max-width: 1200px; width: 100%; border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0,0,0,.25); overflow: hidden;
  animation: modalIn .2s ease-out; }}
@keyframes modalIn {{ from {{ opacity: 0; transform: translateY(20px); }}
  to {{ opacity: 1; transform: translateY(0); }} }}
.modal header {{ padding: 14px 20px; border-bottom: 1px solid var(--grey);
  display: flex; justify-content: space-between; align-items: center;
  background: var(--grey-soft); }}
.modal header h2 {{ margin: 0; font-size: 15px; color: var(--pink); }}
.modal header .close {{ cursor: pointer; background: none; border: none;
  font-size: 22px; color: var(--muted); transition: color .15s; }}
.modal header .close:hover {{ color: var(--red); }}
.modal .body {{ padding: 14px 20px; max-height: 70vh; overflow: auto; }}
.modal .meta {{ color: var(--muted); font-size: 11.5px; margin-bottom: 10px; }}
.modal table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; }}
.modal th, .modal td {{ border: 1px solid var(--grey); padding: 5px 8px;
  text-align: left; vertical-align: top; }}
.modal th {{ background: var(--grey-soft); position: sticky; top: 0; }}
.modal .download {{ margin-left: 10px; font-size: 12px; padding: 6px 12px;
  background: var(--pink); color: #fff; border: none; border-radius: 6px;
  cursor: pointer; transition: background .15s; }}
.modal .download:hover {{ background: #C2185B; }}
.modal .loading {{ color: var(--muted); font-style: italic; padding: 20px 0; }}
.modal .error {{ color: var(--red); padding: 12px; background: #FFEBEE; border-radius: 6px; }}
</style>
</head>
<body>

<header class="main-header">
  <h1>Daily Inbound Call Summary &mdash; Service Queue</h1>
  <div class="sub">
    Source: Metabase &middot; PROD_DB.PUBLIC.AMEYO_CALL_DETAILS_REPORT &middot;
    QUEUE_NAME IN (high_pain_queue, low_pain_queue) &middot;
    CALL_TYPE = inbound.call.dial
  </div>
  <div class="stats">
    <div class="stat-card"><div class="k">Months</div><div class="v">{len(months_sorted)}</div></div>
    <div class="stat-card"><div class="k">Categories</div><div class="v" id="stat-cats">{len(taxonomy)}</div></div>
    <div class="stat-card"><div class="k">Sub-category Issues</div><div class="v" id="stat-codes">{sum(len(c) for _,c in taxonomy)}</div></div>
    <div class="stat-card"><div class="k" id="stat-total-label">Total Inbound Calls</div><div class="v" id="stat-total">-</div></div>
    <div class="stat-card"><div class="k" id="stat-miss-label">Missed Call % (MTD)</div><div class="v" id="stat-miss">-</div></div>
    <div class="stat-card"><div class="k" id="stat-aht-label">Avg AHT</div><div class="v" id="stat-aht">-</div></div>
    <div class="stat-card"><div class="k">Last Refreshed</div><div class="v">{refreshed_at}</div>
      <button onclick="manualRefresh(this)" style="margin-top:4px;padding:3px 10px;font-size:11px;cursor:pointer;border:1px solid #d32f2f;background:#fff;color:#d32f2f;border-radius:4px;font-weight:600;">Refresh Now</button></div>
  </div>
</header>

{highlights_html}

<nav class="tabs">
  {''.join(tabs_buttons)}
  <div class="search-box">
    <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
    <input type="text" id="searchInput" placeholder="Filter categories..." />
  </div>
</nav>

<main>
  <div class="legend">
    <span class="pill">&#x26A0; Threshold</span>
    value <b>&ge; 100 &rarr; +15 %</b> &middot;
    value <b>20 &ndash; 99 &rarr; +25 %</b> &middot;
    value <b>&lt; 20 &rarr; +100 %</b>
    &middot; Double-click any number for raw call records.
  </div>
  {''.join(tabs_panels)}
</main>

<div class="modal-backdrop" id="rawModal">
  <div class="modal">
    <header>
      <h2 id="rawTitle">Raw call records</h2>
      <div>
        <button class="download" id="rawDownload">Download CSV</button>
        <button class="close" id="rawClose">&times;</button>
      </div>
    </header>
    <div class="body">
      <div class="meta" id="rawMeta"></div>
      <div id="rawContent"><div class="loading">Loading…</div></div>
    </div>
  </div>
</div>

<footer>Wiom · Inbound Call Dashboard · auto-refreshed hourly 08:00–22:00 IST (incl. same-day live data) · Use "Refresh Now" for instant update</footer>

<script>
function manualRefresh(btn) {{
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  btn.style.background = '#fff3e0';
  btn.style.color = '#e65100';
  fetch('/refresh').then(r => r.json()).then(d => {{
    btn.textContent = 'Reloading...';
    setTimeout(() => location.reload(), 35000);
  }}).catch(e => {{
    btn.textContent = 'Error — retry';
    btn.disabled = false;
    btn.style.background = '#fff';
    btn.style.color = '#d32f2f';
  }});
}}
const PER_TAB_TOTALS = {json.dumps(per_tab_totals)};
const CODES_PER_TAB  = {json.dumps(codes_per_tab)};
const PER_TAB_MISSPCT = {json.dumps(per_tab_misspct)};
const PER_TAB_AHT    = {json.dumps(per_tab_aht)};
const statTotal = document.getElementById('stat-total');
const statTotalLabel = document.getElementById('stat-total-label');
const statCodes = document.getElementById('stat-codes');
const statMiss  = document.getElementById('stat-miss');
const statAht   = document.getElementById('stat-aht');

function fmtNum(n) {{ return (n || 0).toLocaleString('en-IN'); }}

const buttons = document.querySelectorAll('.tab-btn');
const panels  = document.querySelectorAll('.tab-panel');
function activate(id) {{
  buttons.forEach(b => b.classList.toggle('active', b.dataset.target === id));
  panels.forEach (p => p.classList.toggle('active', p.id === id));
  try {{ localStorage.setItem('wiom.dash.tab', id); }} catch(e) {{}}
  // per-tab stats
  if (id === 'tab-mom') {{
    statTotalLabel.textContent = 'Total Inbound Calls (All Months)';
  }} else {{
    const label = document.querySelector('.tab-btn[data-target="' + id + '"]').textContent;
    statTotalLabel.textContent = 'Total Inbound Calls (' + label + ')';
  }}
  statTotal.textContent = fmtNum(PER_TAB_TOTALS[id] || 0);
  if (CODES_PER_TAB[id] !== undefined) statCodes.textContent = CODES_PER_TAB[id];
  statMiss.textContent = (PER_TAB_MISSPCT[id] !== undefined) ? PER_TAB_MISSPCT[id] + '%' : '-';
  statAht.textContent  = PER_TAB_AHT[id] || '-';
  // toggle highlight bars per tab
  document.querySelectorAll('.hl-tab').forEach(el => {{
    el.classList.toggle('active', el.dataset.tab === id);
  }});
  if (id === 'tab-mom') renderChart();
}}
buttons.forEach(b => b.addEventListener('click', () => activate(b.dataset.target)));
const saved = (function(){{ try {{ return localStorage.getItem('wiom.dash.tab'); }} catch(e) {{ return null; }} }})();
activate(saved && document.getElementById(saved) ? saved : buttons[0].dataset.target);

// ---------------- raw-data drill-through ----------------
const modal      = document.getElementById('rawModal');
const modalTitle = document.getElementById('rawTitle');
const modalMeta  = document.getElementById('rawMeta');
const modalBody  = document.getElementById('rawContent');
const modalDL    = document.getElementById('rawDownload');
let currentRows = [];
let currentName = 'raw.csv';

document.getElementById('rawClose').onclick = () => modal.classList.remove('open');
modal.addEventListener('click', e => {{ if (e.target === modal) modal.classList.remove('open'); }});
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') modal.classList.remove('open'); }});

document.querySelectorAll('table.dash td.num').forEach(td => {{
  if (!td.dataset.scope) return;
  td.addEventListener('dblclick', () => openDrill(td));
}});

// ---------------- collapsible categories ----------------
document.querySelectorAll('td[data-toggle]').forEach(td => {{
  td.addEventListener('click', () => {{
    const cls = td.dataset.toggle;
    const icon = td.querySelector('.toggle-icon');
    const rows = td.closest('table').querySelectorAll('tr.code-of-' + cls);
    const isHidden = rows.length && rows[0].classList.contains('hidden');
    rows.forEach(r => r.classList.toggle('hidden', !isHidden));
    if (icon) icon.classList.toggle('collapsed', !isHidden);
  }});
}});

// ---------------- search / filter ----------------
const searchInput = document.getElementById('searchInput');
searchInput.addEventListener('input', () => {{
  const q = searchInput.value.trim().toLowerCase();
  document.querySelectorAll('.tab-panel.active table.dash tbody tr').forEach(tr => {{
    if (tr.classList.contains('row-total')) return;
    const label = (tr.querySelector('td.disp') || {{}}).textContent || '';
    if (!q) {{
      tr.classList.remove('search-miss');
    }} else {{
      tr.classList.toggle('search-miss', !label.toLowerCase().includes(q));
    }}
  }});
}});
// clear search on tab switch
buttons.forEach(b => b.addEventListener('click', () => {{
  searchInput.value = '';
  document.querySelectorAll('tr.search-miss').forEach(r => r.classList.remove('search-miss'));
}}));

function openDrill(td) {{
  const p = new URLSearchParams({{
    cls:   td.dataset.cls,
    code:  td.dataset.code || '',
    scope: td.dataset.scope,
    date:  td.dataset.date || '',
    ym:    td.dataset.ym   || '',
  }});
  modal.classList.add('open');
  modalBody.innerHTML = '<div class="loading">Loading raw records from Metabase…</div>';
  modalDL.style.display = 'none';
  currentRows = [];

  const scopeLabel = (td.dataset.scope === 'day') ? ('Day: ' + td.dataset.date)
                    : (td.dataset.scope === 'mtd') ? ('Month: ' + td.dataset.ym + ' (MTD)')
                    : ('Previous month: ' + td.dataset.ym);
  const descriptor = td.dataset.cls + (td.dataset.code ? ' › ' + td.dataset.code : '');
  modalTitle.textContent = descriptor;
  modalMeta.textContent  = scopeLabel;
  currentName = ('raw_' + descriptor + '_' + (td.dataset.date || td.dataset.ym) + '.csv')
                .replace(/[^A-Za-z0-9._-]+/g, '_');

  fetch('/raw?' + p.toString())
    .then(r => r.json())
    .then(j => {{
      if (j.error) {{
        modalBody.innerHTML = '<div class="error">' + j.error + '</div>';
        return;
      }}
      currentRows = j.rows || [];
      const cols = j.cols || [];
      modalMeta.textContent = scopeLabel + '   ·   ' + currentRows.length + ' records';
      if (currentRows.length === 0) {{
        modalBody.innerHTML = '<div class="loading">No records for this selection.</div>';
        return;
      }}
      let html = '<table><thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
      currentRows.slice(0, 500).forEach(row => {{
        html += '<tr>' + row.map(v => '<td>' + (v == null ? '' : String(v).replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}})[c])) + '</td>').join('') + '</tr>';
      }});
      html += '</tbody></table>';
      if (currentRows.length > 500) {{
        html += '<div class="meta" style="margin-top:8px">Showing first 500 of ' + currentRows.length + ' rows. Use Download CSV for the full set.</div>';
      }}
      modalBody.innerHTML = html;
      modalDL.style.display = '';
      modalDL.onclick = () => {{
        const csv = [cols.join(',')].concat(
          currentRows.map(r => r.map(v => {{
            const s = v == null ? '' : String(v);
            return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
          }}).join(','))
        ).join('\\n');
        const blob = new Blob([csv], {{type: 'text/csv'}});
        const url  = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = currentName; a.click();
        URL.revokeObjectURL(url);
      }};
    }})
    .catch(err => {{
      modalBody.innerHTML = '<div class="error">Failed to load raw data: ' + err + '<br><br>'
        + 'Drill-through requires the custom Python server (scripts\\\\serve_dashboard.bat). '
        + 'If you opened the file directly, start the server first.</div>';
    }});
}}

// ---- MoM charts (flipped axes: x=months, one line per class/code) ----
const MOM_MONTH_LABELS  = {json.dumps(mom_month_labels)};
const MOM_CAT_SERIES    = {json.dumps(mom_cat_series)};
const MOM_CAT_COLORS    = {json.dumps(mom_cat_colors)};
const TOP15_CODE_SERIES = {json.dumps(top15_code_series)};
const TOP15_CODE_COLORS = {json.dumps(top15_code_colors)};
const CODE_CLASS_MAP    = {json.dumps(code_class_map_js)};

let chartInstance     = null;
let top10ChartInstance = null;
let _activeFilter     = '';   // '' = all

const CHART_OPTS_BASE = {{
  responsive: true,
  maintainAspectRatio: false,
  devicePixelRatio: window.devicePixelRatio || 2,
  animation: {{ duration: 250 }},
  plugins: {{
    legend: {{
      position: 'right',
      labels: {{ boxWidth: 12, padding: 10, font: {{ size: 11 }} }}
    }}
  }},
  scales: {{
    x: {{ ticks: {{ font: {{ size: 12 }}, color: '#333' }},
         grid: {{ color: '#eee' }} }},
    y: {{ title: {{ display: true, text: 'Avg calls / day', font: {{ size: 11 }} }},
         ticks: {{ font: {{ size: 11 }} }}, grid: {{ color: '#eee' }} }}
  }}
}};

function buildCatDatasets(filter) {{
  return Object.entries(MOM_CAT_SERIES)
    .filter(([cls]) => !filter || cls === filter)
    .map(([cls, data]) => ({{
      label: cls,
      data: data,
      borderColor: MOM_CAT_COLORS[cls],
      backgroundColor: MOM_CAT_COLORS[cls] + '22',
      tension: 0.3, borderWidth: 2.5, pointRadius: 5,
      pointHoverRadius: 7,
    }}));
}}

function buildCodeDatasets(filter) {{
  return Object.entries(TOP15_CODE_SERIES)
    .filter(([code]) => !filter || CODE_CLASS_MAP[code] === filter)
    .map(([code, data]) => ({{
      label: code,
      data: data,
      borderColor: TOP15_CODE_COLORS[code],
      backgroundColor: TOP15_CODE_COLORS[code] + '22',
      tension: 0.3, borderWidth: 2.5, pointRadius: 5,
      pointHoverRadius: 7,
    }}));
}}

function renderChart() {{
  setTimeout(function() {{
    // Chart 1 — categories
    if (!chartInstance) {{
      const ctx = document.getElementById('momChart').getContext('2d');
      chartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{ labels: MOM_MONTH_LABELS, datasets: buildCatDatasets(_activeFilter) }},
        options: Object.assign({{}}, CHART_OPTS_BASE, {{
          plugins: Object.assign({{}}, CHART_OPTS_BASE.plugins, {{
            title: {{ display: true,
              text: 'M.o.M Avg Daily Inbound Calls — Category Wise',
              font: {{ size: 14, weight: 'bold' }}, padding: {{ bottom: 12 }} }}
          }})
        }})
      }});
    }} else {{
      chartInstance.data.datasets = buildCatDatasets(_activeFilter);
      chartInstance.update();
    }}

    // Chart 2 — sub-categories
    if (!top10ChartInstance) {{
      const ctx2 = document.getElementById('top10Chart').getContext('2d');
      top10ChartInstance = new Chart(ctx2, {{
        type: 'line',
        data: {{ labels: MOM_MONTH_LABELS, datasets: buildCodeDatasets(_activeFilter) }},
        options: Object.assign({{}}, CHART_OPTS_BASE, {{
          plugins: Object.assign({{}}, CHART_OPTS_BASE.plugins, {{
            title: {{ display: true,
              text: 'M.o.M Avg Daily Inbound Calls — Top 15 Sub-categories',
              font: {{ size: 14, weight: 'bold' }}, padding: {{ bottom: 12 }} }}
          }})
        }})
      }});
    }} else {{
      top10ChartInstance.data.datasets = buildCodeDatasets(_activeFilter);
      top10ChartInstance.update();
    }}
  }}, 50);
}}

function applyChartFilter(cls) {{
  _activeFilter = cls || '';
  const note = document.getElementById('chartFilterNote');
  if (_activeFilter) {{
    note.textContent = 'Showing: ' + _activeFilter;
    note.style.display = '';
  }} else {{
    note.style.display = 'none';
  }}
  if (chartInstance) {{
    chartInstance.data.datasets = buildCatDatasets(_activeFilter);
    chartInstance.update();
  }}
  if (top10ChartInstance) {{
    top10ChartInstance.data.datasets = buildCodeDatasets(_activeFilter);
    top10ChartInstance.update();
  }}
}}
</script>
</body>
</html>'''

OUT.write_text(PAGE, encoding="utf-8")
print(f"Wrote {OUT}")
print(f"Size: {OUT.stat().st_size:,} bytes")
