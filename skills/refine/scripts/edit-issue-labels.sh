#!/usr/bin/env bash
# Add or remove issue labels under the allow-list, the veto and the agent-ready rule.
# See refine_write.py for the rules this enforces (issue #1321).
set -euo pipefail
exec python3 "$(dirname "$0")/refine_write.py" edit-labels "$@"
