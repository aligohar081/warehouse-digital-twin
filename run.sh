#!/usr/bin/env bash
# Warehouse Digital Twin — one-command launcher for Ubuntu.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "==> Creating virtual environment in $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

mkdir -p logs data

HOST="${WAREHOUSE_HOST:-127.0.0.1}"
PORT="${WAREHOUSE_PORT:-5000}"
LAYOUT="${WAREHOUSE_LAYOUT:-distribution_center}"

echo
echo "======================================================"
echo "  WAREHOUSE DIGITAL TWIN — ROBOT CONTROL CENTRE"
echo "  Dashboard: http://$HOST:$PORT"
echo "  Floor: $LAYOUT (set WAREHOUSE_LAYOUT=classic for the classic floor)"
echo "  Stop with Ctrl+C"
echo "======================================================"
echo

exec env WAREHOUSE_HOST="$HOST" WAREHOUSE_PORT="$PORT" WAREHOUSE_LAYOUT="$LAYOUT" python -m backend.app
