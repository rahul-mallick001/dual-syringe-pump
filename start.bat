@echo off
REM ============================================================
REM  Syringe Pump - double-click launcher
REM  Starts the web UI and opens it in your browser.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   Starting Syringe Pump...
echo.

REM Use the Python launcher if available, else fall back to python on PATH.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py app.py
) else (
    python app.py
)

REM If Python exits immediately (e.g. not installed) keep the window open
REM so the error message stays readable.
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   The app exited with an error ^(code %ERRORLEVEL%^).
    echo   Make sure Python 3.10+ is installed and on PATH.
    echo.
    pause
)
endlocal
