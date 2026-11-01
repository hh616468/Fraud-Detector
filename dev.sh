#!/usr/bin/env bash
# Task runner (WSL / Linux). Usage: ./dev.sh <task>
set -euo pipefail
cd "$(dirname "$0")"
VENV="${VENV:-$HOME/.venvs/fraud-detector}"
PY="$VENV/bin/python"

case "${1:-help}" in
  setup)    python3 -m venv --without-pip "$VENV" 2>/dev/null || true
            [ -x "$VENV/bin/pip" ] || { curl -sSfL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && "$PY" /tmp/get-pip.py -q; }
            "$VENV/bin/pip" install -q -e ".[dev]" ;;
  test)     "$PY" -m pytest "${@:2}" ;;
  lint)     "$VENV/bin/ruff" check . ;;
  smoke)    "$PY" -m coach.smoke "${@:2}" ;;
  personas) "$PY" -m data.personas "${@:2}" ;;
  data)     "$PY" -m data.build "${@:2}" ;;
  validate) "$PY" -m data.validate "${@:2}" ;;
  seed)     "$PY" -m wallet.seed "${@:2}" ;;
  run)      "$PY" -m uvicorn wallet.api:app --host 0.0.0.0 --port "${PORT:-8000}" "${@:2}" ;;
  eval)     "$PY" -m eval.run_eval "${@:2}" ;;
  freeze)   "$PY" -m eval.freeze "${@:2}" ;;
  coach-eval) "$PY" -m eval.coach_eval "${@:2}" ;;
  *) echo "tasks: setup test lint smoke personas data validate seed run eval freeze coach-eval" ;;
esac
