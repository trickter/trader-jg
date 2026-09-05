#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -e .
fi
.venv/bin/python -m radar init-db
.venv/bin/python -m radar collector &
collector_pid=$!
trap 'kill "$collector_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/python -m radar serve
