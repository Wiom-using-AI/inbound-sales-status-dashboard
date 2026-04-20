@echo off
REM One-time installer: registers a Windows Scheduled Task that runs the
REM Sales & Status Queue dashboard refresh every day at 08:30 local time.

schtasks /Create ^
  /TN "WiomInboundSalesStatusDashboard" ^
  /TR "\"C:\Users\Rashi Mehra\Desktop\my_claude_project\scripts\refresh_sales_dashboard.bat\"" ^
  /SC DAILY ^
  /ST 08:30 ^
  /F

schtasks /Query /TN "WiomInboundSalesStatusDashboard"
