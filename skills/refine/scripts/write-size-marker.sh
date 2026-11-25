#!/usr/bin/env bash
# Record a sizing judgement as a <!-- refine:size N --> comment.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" size-marker "$@"
