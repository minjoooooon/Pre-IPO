@echo off
title Pre-IPO Normalizer

REM ----------------------------------------------------------------
REM Optional: pin a fixed admin token across restarts.
REM   - Leave commented to use the random token generated each boot
REM     (printed in the startup log as "admin_token token=...").
REM   - Uncomment + edit the line below to fix it. This file is in
REM     .gitignore-safe territory (root-level .bat is not tracked
REM     unless explicitly added), but treat the token as a secret.
REM ----------------------------------------------------------------
REM set ADMIN_TOKEN=replace-me-with-your-secret

cd /d "%~dp0backend"

echo ===========================================
echo   Pre-IPO Normalizer
echo   URL:   http://127.0.0.1:8000/
echo   Quit:  Ctrl+C  (or close this window)
echo ===========================================
echo.

REM Open the dashboard in the default browser after a short delay so
REM uvicorn has time to bind :8000. /b keeps it in the background so
REM this window stays focused on the server log.
start /b "" cmd /c "timeout /t 3 /nobreak >nul ^&^& start http://127.0.0.1:8000/"

python -m app.main

echo.
echo (Server stopped. Press any key to close this window.)
pause >nul
