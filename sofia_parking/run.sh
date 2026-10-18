#!/bin/bash
# Run the daily update, creating/repairing the venv first if needed.
# Usage: ./run.sh [sofia-parking args...]   (default: -v run)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

VENV=.venv
# A venv that was moved keeps stale shebangs in its scripts, so check that the entry point actually runs.
if ! "$VENV/bin/sofia-parking" --help >/dev/null 2>&1; then
    echo "setting up $VENV ..." >&2
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q --upgrade pip
    "$VENV/bin/python" -m pip install -q -e .
fi

if [ ! -f config.toml ]; then
    echo "config.toml missing: cp config.example.toml config.toml and set [target]" >&2
    exit 1
fi

if [ $# -eq 0 ]; then
    set -- -v run
fi
exec "$VENV/bin/sofia-parking" "$@"
