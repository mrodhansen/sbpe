#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1
if [[ -d .venv ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
exec python3 loader.py
