#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -e .

if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i python server.py
else
    python server.py
fi
