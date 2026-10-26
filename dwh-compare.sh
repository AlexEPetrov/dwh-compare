#!/usr/bin/env bash
# Run CLI without installing the package (PYTHONPATH=src).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "No .venv — run ./setup.sh first" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m dwh_compare "$@"
