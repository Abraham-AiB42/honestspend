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
echo "LedgerRing engine → http://127.0.0.1:7420"
echo "Glance (Mac/Linux/phone) → http://127.0.0.1:7420/glance"
echo "Tip: ./scripts/start-glance.sh opens Glance in a browser"
exec python -m financial_os.cli serve "$@"
