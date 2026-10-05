#!/bin/bash
cd "$(dirname "$0")" || exit 1
if command -v python3.12 >/dev/null 2>&1; then
    python3.12 scripts/launch_local.py "$@"
else
    python3 scripts/launch_local.py "$@"
fi
status=$?
if [ "$status" -ne 0 ]; then
    echo "See docs/06_LOCAL_SETUP.html. Press Enter to close."
    read -r answer
fi
exit "$status"
