#!/usr/bin/env bash
# Turn the job red when the model step did not finish cleanly (issue #1321).
#
# The step outcome alone is not a completion signal: claude-code-action can
# exit 0 while its final result carries is_error: true. This is the check
# .github/workflows/claude-review.yml runs, read from the environment:
#   OUTCOME     the model step's outcome
#   CONCLUSION  the action's own conclusion output
#   EXEC_FILE   the action's execution log (a JSON array or JSON lines)
set -uo pipefail

reason=""
if [ "${OUTCOME:-}" != "success" ] || [ "${CONCLUSION:-}" != "success" ]; then
  reason="step outcome=${OUTCOME:-unset}, action conclusion=${CONCLUSION:-unset}"
elif [ -z "${EXEC_FILE:-}" ] || [ ! -s "$EXEC_FILE" ]; then
  reason="no execution log was produced"
elif ! jq -es '[.[] | if type == "array" then .[] else . end] | map(select(.type == "result")) | last | (. != null) and (.is_error == false)' "$EXEC_FILE" >/dev/null 2>&1; then
  reason="final result entry missing or is_error:true"
fi

if [ -n "$reason" ]; then
  echo "::error::The refiner's model step did not complete: ${reason}. Nothing from it was applied."
  exit 1
fi
echo "The model step completed."
