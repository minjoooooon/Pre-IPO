@echo off
REM ----------------------------------------------------------------
REM Pre-IPO Normalizer launcher (silent / hidden console)
REM
REM   Double-click runs the backend with NO visible console window.
REM   Browser opens http://127.0.0.1:8000/ after ~3s.
REM   Use stop.bat (or close in Task Manager) to shut down.
REM
REM   Why PowerShell + Start-Process instead of pythonw:
REM     pythonw.exe sets sys.stdout / sys.stderr to None on Windows.
REM     The backend installs a logging.StreamHandler() (stderr by
REM     default) — emitting against a None stream raises AttributeError
REM     and kills the process at the first log call. Running python.exe
REM     inside a hidden window keeps stdout/stderr alive (just invisible)
REM     so logging works exactly like the visible mode.
REM
REM   Logs still go to backend/logs/app.log (RotatingFileHandler),
REM   so the auto-generated ADMIN_TOKEN can be retrieved from there:
REM     Get-Content backend\logs\app.log | Select-String admin_token
REM ----------------------------------------------------------------

REM Optional: pin a fixed admin token across restarts.
REM set ADMIN_TOKEN=replace-me-with-your-secret

REM Hidden launch. -WindowStyle Hidden hides this PowerShell stub;
REM Start-Process -WindowStyle Hidden hides the python window itself.
REM
REM -RedirectStandardError captures pre-logging-configured failures
REM (import errors, syntax errors, the brief window before _configure_logging
REM runs in __main__). Once logging is up, uvicorn bind errors go straight to
REM backend/logs/app.log; bootstrap.err only catches the earliest crashes.
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath python -ArgumentList '-m','app.main' -WorkingDirectory '%~dp0backend' -WindowStyle Hidden -RedirectStandardError '%~dp0backend\logs\bootstrap.err' -RedirectStandardOutput '%~dp0backend\logs\bootstrap.out'"

REM Open the dashboard after uvicorn has time to bind :8000.
REM /B keeps no extra window; the cmd subshell self-exits after starting the URL.
start "" /B cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000/"

REM This .bat exits immediately so its own console flashes for <0.5s
REM and disappears. The backend keeps running as a detached process.
exit /B 0
