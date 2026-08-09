#!/usr/bin/env bash
# Mac / Linux: start fiscal engine + open multi-platform Glance UI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e .

export FOS_HOST="${FOS_HOST:-127.0.0.1}"
export FOS_PORT="${FOS_PORT:-7420}"

echo "Floatpile engine → http://${FOS_HOST}:${FOS_PORT}"
echo "Glance UI        → http://${FOS_HOST}:${FOS_PORT}/glance"
echo "API docs         → http://${FOS_HOST}:${FOS_PORT}/docs"

# Start server in background if not already healthy
if ! curl -sf "http://${FOS_HOST}:${FOS_PORT}/api/health" >/dev/null 2>&1; then
  python -m financial_os.cli serve --host "$FOS_HOST" --port "$FOS_PORT" &
  SERVER_PID=$!
  echo "Started engine pid $SERVER_PID"
  for i in $(seq 1 30); do
    if curl -sf "http://${FOS_HOST}:${FOS_PORT}/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
else
  echo "Engine already running"
fi

python -m financial_os.cli glance --open --host "$FOS_HOST" --port "$FOS_PORT" || true
echo "Press Ctrl+C in the serve terminal to stop, or kill the background job."
# If we started the server, wait on it
if [[ -n "${SERVER_PID:-}" ]]; then
  wait "$SERVER_PID"
fi
