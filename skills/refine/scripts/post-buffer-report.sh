#!/usr/bin/env bash
# Keep the ready-buffer report on the questions issue up to date.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" report "$@"
