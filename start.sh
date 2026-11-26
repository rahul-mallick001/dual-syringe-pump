#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "First-time run detected. Running setup.sh..."
    bash setup.sh
fi

.venv/bin/python app.py
