#!/usr/bin/env bash
# Usage: ./run.sh          (live data)
#        ./run.sh demo     (built-in demo data, no internet needed)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
[ "$1" = "demo" ] && export DEMO_MODE=1
echo "Open http://127.0.0.1:8000"
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
