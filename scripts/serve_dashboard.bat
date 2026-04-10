@echo off
REM Local HTTP server for the Inbound Call Dashboard (with Metabase drill-through).
REM Open: http://localhost:8765/
REM Stop: close this window or Ctrl+C.

echo.
echo  Wiom Inbound Call Dashboard
echo  --------------------------------
echo  URL:  http://localhost:8765/
echo.
python "C:\Users\Rashi Mehra\Desktop\my_claude_project\scripts\serve_dashboard.py"
