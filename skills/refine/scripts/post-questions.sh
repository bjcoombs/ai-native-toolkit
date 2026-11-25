#!/usr/bin/env bash
# Post every question from this run in one comment on the questions issue.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" questions "$@"
