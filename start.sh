#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

find_python() {
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && \
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "Python 3.11 or newer is required."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating virtual environment..."
  "$PYTHON" -m venv .venv
elif ! .venv/bin/python --version >/dev/null 2>&1; then
  echo "Existing virtual environment is invalid. Repairing it..."
  "$PYTHON" -m venv --clear .venv
fi

if ! .venv/bin/python -c 'import cloudriver_manager, playwright' >/dev/null 2>&1; then
  echo "First run: installing CloudRiver Manager and browser support..."
  .venv/bin/python -m pip install -e '.[browser]'
fi

exec .venv/bin/python -m cloudriver_manager
