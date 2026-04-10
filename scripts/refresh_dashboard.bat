@echo off
REM Daily refresh for the Inbound Call Service-Queue dashboard.
REM Runs: pull_ameyo.py  ->  build_dashboard.py
REM Logs: logs\refresh_YYYY-MM-DD.log

setlocal
set PROJECT=C:\Users\Rashi Mehra\Desktop\my_claude_project
set LOGDIR=%PROJECT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set dt=%%a
set TODAY=%dt:~0,4%-%dt:~4,2%-%dt:~6,2%
set LOG=%LOGDIR%\refresh_%TODAY%.log

echo ==== %date% %time% : starting refresh ==== >> "%LOG%"
cd /d "%PROJECT%"

python "%PROJECT%\scripts\pull_ameyo.py"       >> "%LOG%" 2>&1
if errorlevel 1 (
    echo pull_ameyo FAILED >> "%LOG%"
    exit /b 1
)

python "%PROJECT%\scripts\build_dashboard.py"  >> "%LOG%" 2>&1
if errorlevel 1 (
    echo build_dashboard FAILED >> "%LOG%"
    exit /b 1
)

python "%PROJECT%\scripts\build_html.py"       >> "%LOG%" 2>&1
if errorlevel 1 (
    echo build_html FAILED >> "%LOG%"
    exit /b 1
)

echo ==== %date% %time% : refresh done ==== >> "%LOG%"
endlocal
exit /b 0
