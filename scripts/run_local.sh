#!/usr/bin/env bash
# Local test run (does NOT need GitHub).
#   ./scripts/run_local.sh            -> dry run (discovery only, no uploads)
#   RUN_LIVE=1 ./scripts/run_local.sh -> full run (needs DISCORD_WEBHOOK_URL)
#   ./scripts/run_local.sh --diagnose -> print a streamrecorder report
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [ "${RUN_LIVE:-0}" != "1" ]; then
  export DRY_RUN=1
fi

python -m app.main "$@"
