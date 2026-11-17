#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Also supports installation as run.sh at the Pi root for older service units.
if [[ ! -d pi_vision ]]; then
  cd ..
fi
if [[ ! -x .venv/bin/python ]]; then
  echo 'Run bash scripts/setup.sh first.' >&2
  exit 1
fi
exec .venv/bin/python -u -m pi_vision.camera.app "$@"
