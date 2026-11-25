#!/usr/bin/env bash
# Reopen an issue the refiner closed as a duplicate, on a /reopen comment.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" reopen "$@"
