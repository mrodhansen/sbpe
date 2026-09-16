#!/bin/bash
# Steam launch wrapper. Steam will pass %command% as extra args; ignore them
# and start StarBreak through SBPE instead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONNOUSERSITE=1
export SBPE_WAIT=1
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "SBPE: missing $PY — run $ROOT/build.sh first" >&2
  exit 1
fi
exec "$PY" "$ROOT/loader.py"
