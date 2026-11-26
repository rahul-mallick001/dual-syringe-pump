@echo off
setlocal EnableDelayedExpansion
title Dual Syringe Pump Controller
cd /d "%~dp0"

echo ============================================================
echo   Dual Syringe Pump Controller
echo ============================================================
echo.

REM 1. Check if virtual environment exists and has required packages
set "RUN_PYTHON="
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -c "import fastapi, uvicorn, serial, pydantic" >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        set "RUN_PYTHON=.venv\Scripts\python.exe"
    )
)

REM 2. If virtual environment is missing or incomplete, auto-run setup
if "!RUN_PYTHON!"=="" (
    echo First-time run or missing dependencies detected.
    echo Running automatic one-click setup...
    echo.
    call "%~dp0setup.bat"
    if exist ".venv\Scripts\python.exe" (
        .venv\Scripts\python.exe -c "import fastapi, uvicorn, serial, pydantic" >nul 2>nul
        if !ERRORLEVEL! EQU 0 (
            set "RUN_PYTHON=.venv\Scripts\python.exe"
        )
    )
)

REM 3. Fallback: check system Python if .venv could not be created
if "!RUN_PYTHON!"=="" (
    where py >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        py -3 -c "import fastapi, uvicorn, serial, pydantic" >nul 2>nul
        if !ERRORLEVEL! EQU 0 set "RUN_PYTHON=py -3"
    )
)
if "!RUN_PYTHON!"=="" (
    where python >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        python -c "import fastapi, uvicorn, serial, pydantic" >nul 2>nul
        if !ERRORLEVEL! EQU 0 set "RUN_PYTHON=python"
    )
)

if "!RUN_PYTHON!"=="" (
    echo.
    echo [ERROR] Could not start the application.
    echo Please run 'setup.bat' or ensure Python 3.10+ is installed on PATH.
    echo.
    pause
    exit /b 1
)

REM 4. Start the application (opens browser automatically at http://127.0.0.1:8077)
echo Starting Dual Syringe Pump server...
echo.
!RUN_PYTHON! app.py

if !ERRORLEVEL! NEQ 0 (
    echo.
    echo The application exited with error code !ERRORLEVEL!.
    pause
)
endlocal
