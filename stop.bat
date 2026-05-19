@echo off
REM Pre-IPO Normalizer shutdown (silent).
REM netstat+taskkill is cmd-native; no PowerShell quoting fragility.
REM Filters by LISTENING state on :8000 so only the backend server is killed,
REM not random client connections that happen to hit :8000.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr LISTENING ^| findstr ":8000 "') do taskkill /F /PID %%a >nul 2>&1
exit /B 0
