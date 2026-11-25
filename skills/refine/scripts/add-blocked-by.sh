#!/usr/bin/env bash
# Add a blocked-by edge through the issue dependencies API.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" blocked-by "$@"
