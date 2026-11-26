#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  Dual Syringe Pump Controller - One-Click Setup"
echo "============================================================"
echo ""

# 1. Find Python 3
PYTHON=""
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
fi

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3 was not found. Please install Python 3.10+."
    exit 1
fi

echo "[OK] Found $($PYTHON --version)"
echo ""

# 2. Create virtual environment
if [ ! -f ".venv/bin/python" ]; then
    echo "[1/3] Creating virtual environment (.venv)..."
    $PYTHON -m venv .venv
    echo "      Virtual environment created."
else
    echo "[1/3] Existing virtual environment found (.venv)."
fi
echo ""

# 3. Install dependencies
echo "[2/3] Installing dependencies from requirements.txt..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet
echo "      Dependencies installed."
echo ""

# 4. Verification
echo "[3/3] Verifying installation..."
.venv/bin/python -c "import fastapi, uvicorn, serial, pydantic; print('      Verification passed: all core libraries load correctly.')"
echo ""

echo "============================================================"
echo "  Setup complete! Run ./start.sh to launch the application."
echo "============================================================"
