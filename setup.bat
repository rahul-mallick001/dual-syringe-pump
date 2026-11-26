@echo off
setlocal EnableDelayedExpansion
title Dual Syringe Pump - Setup
cd /d "%~dp0"

echo ============================================================
echo   Dual Syringe Pump Controller - One-Click Setup
echo ============================================================
echo.

REM 1. Find Python
set "PYTHON_EXE="
where py >nul 2>nul
if !ERRORLEVEL! EQU 0 (
    py -3 --version >nul 2>nul
    if !ERRORLEVEL! EQU 0 set "PYTHON_EXE=py -3"
)
if "!PYTHON_EXE!"=="" (
    where python >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        python --version >nul 2>nul
        if !ERRORLEVEL! EQU 0 set "PYTHON_EXE=python"
    )
)

if "!PYTHON_EXE!"=="" (
    echo [ERROR] Python 3.10+ was not found on your system.
    echo.
    echo   Please install Python 3 from https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during installation.
    echo.
    echo   Alternatively, if you have winget installed, run:
    echo     winget install Python.Python.3.11
    echo.
    pause
    exit /b 1
)

echo [OK] Found Python:
!PYTHON_EXE! --version
echo.

REM 2. Create or verify virtual environment (.venv)
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment in .venv ...
    !PYTHON_EXE! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo       Virtual environment created successfully.
) else (
    echo [1/4] Existing virtual environment found in .venv.
)
echo.

REM 3. Upgrade pip & Install requirements
echo [2/4] Installing / updating dependencies from requirements.txt ...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if !ERRORLEVEL! NEQ 0 (
    echo [WARNING] Retrying install with verbose output...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)
echo       All Python dependencies installed successfully.
echo.

REM 4. Self-test validation
echo [3/4] Verifying installation...
.venv\Scripts\python.exe -c "import fastapi, uvicorn, serial, pydantic; print('      Verification passed: all core libraries load correctly.')"
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Verification failed.
    pause
    exit /b 1
)
echo.

REM 5. Desktop Shortcut Creation
echo [4/4] Creating Desktop Shortcut...
powershell -NoProfile -Command "$lnk = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Dual Syringe Pump.lnk'); if (Test-Path $lnk) { Remove-Item $lnk -Force }; $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($lnk); $s.TargetPath = [System.IO.Path]::Combine('%~dp0', 'start.bat'); $s.WorkingDirectory = '%~dp0'; $s.Description = 'Dual Syringe Pump Controller'; $s.Save()" >nul 2>nul
if !ERRORLEVEL! EQU 0 (
    echo       Desktop shortcut 'Dual Syringe Pump' created!
) else (
    echo       Desktop shortcut skipped.
)
echo.

REM 6. Hardware Driver Reminder
if exist "drivers\CDM212364_Setup_Win_11.exe" (
    echo [INFO] FTDI USB Serial Drivers are available in the 'drivers' folder:
    echo        - Windows 10/11: drivers\CDM212364_Setup_Win_11.exe
    echo        - Older Windows: drivers\CDM v2.12.00 WHQL Certified.exe
    echo.
)

echo ============================================================
echo   Setup Complete! Everything is installed and ready.
echo ============================================================
echo.
echo   To launch the application at any time:
echo     - Double-click 'start.bat' in this folder
echo     - Or use the 'Dual Syringe Pump' shortcut on your Desktop
echo.
set /p LAUNCH_NOW="Would you like to launch the application now? [Y/N, default Y]: "
if /i "!LAUNCH_NOW!"=="" set "LAUNCH_NOW=Y"
if /i "!LAUNCH_NOW!"=="Y" (
    echo.
    echo Starting Dual Syringe Pump...
    start "" "%~dp0start.bat"
)
