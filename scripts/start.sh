#!/usr/bin/env bash
# LedgerRing — macOS / Linux launcher
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -e ".[dev]" -q
echo "LedgerRing → http://127.0.0.1:7420"
exec python -m financial_os.cli serve "$@"
