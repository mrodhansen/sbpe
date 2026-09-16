#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v sdl2-config >/dev/null; then
  echo "sdl2-config not found. Mac: brew install sdl2  Linux: sudo apt install libsdl2-dev" >&2
  exit 1
fi

# arm64 venv: loader + mipmap generation
if [[ ! -d .venv ]]; then
  if [[ -x /opt/homebrew/bin/python3.12 ]]; then
    /opt/homebrew/bin/python3.12 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pip install -r requirements.txt
python3 rectbinpack/rbp_builder.py
deactivate

# x86_64 venv: remote.bin must match the StarBreak client
if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ ! -d .venv-x86 ]]; then
    arch -x86_64 /usr/bin/python3 -m venv .venv-x86
  fi
  # shellcheck source=/dev/null
  source .venv-x86/bin/activate
  arch -x86_64 python -m pip install -r requirements.txt
  export SBPE_ARCH=x86_64
  export ARCHFLAGS='-arch x86_64'
  arch -x86_64 python builder.py
  PYFW=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Python3
  if [[ ! -f "$PYFW" ]]; then
    echo "missing $PYFW" >&2
    exit 1
  fi
  install_name_tool -change '@rpath/Python3.framework/Versions/3.9/Python3' "$PYFW" build/remote.bin
  clang -arch x86_64 -o build/launcher_mac launcher_mac.c
  codesign -s - --force build/remote.bin build/launcher_mac
  deactivate
else
  # shellcheck source=/dev/null
  source .venv/bin/activate
  python3 builder.py
  deactivate
fi
echo "build ok: build/remote.bin"
file build/remote.bin
