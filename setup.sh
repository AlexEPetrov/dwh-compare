#!/usr/bin/env bash
# Bootstrap on the server: venv + pip deps. No build backend (no hatchling).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Need python3.12 (got none). Set PYTHON=/path/to/python3.12" >&2
  exit 1
fi

ver="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [[ "$ver" != "3.12" ]]; then
  echo "Need Python 3.12, got $ver ($PYTHON)" >&2
  exit 1
fi

echo "Using: $($PYTHON --version)"

if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
  echo "Created .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p config
if [[ ! -f config/config.yaml ]]; then
  cp config/config.example.yaml config/config.yaml
  echo "Created config/config.yaml — edit connection settings"
fi
if [[ ! -f config/registry.yaml ]]; then
  cp config/registry.example.yaml config/registry.yaml
  echo "Created config/registry.yaml — edit tables registry"
fi

chmod +x dwh-compare.sh

echo
echo "Ready:"
echo "  ./dwh-compare.sh run -c config/config.yaml -r config/registry.yaml -p yesterday"
