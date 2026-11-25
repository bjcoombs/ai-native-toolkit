#!/usr/bin/env bash
# Close an exact duplicate, under the weekly close cap, with the /reopen route.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" close-duplicate "$@"
