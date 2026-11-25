#!/usr/bin/env bash
# Name likely duplicates of an issue in one comment. Never closes anything.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" comment-duplicates "$@"
