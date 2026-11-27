# The tracking file: reconciliation, structure and CRUD

Step 2 of the marathon skill: reconciling an existing tracking file, the file's structure, and the jq commands that read and write it.

**Reconciliation** (run at start if tracking file exists):
1. Read unit status via the adapter's enumerate operation.
2. Cross-reference each tracking entry:
   - Source `done` but tracking `working` → merged externally. Remove from tracking.
   - Source `in-progress` but PR merged on GitHub → mark unit done, remove from tracking.
   - Source `pending` but tracking has PR → stale entry. Remove, check cleanup needed.
   - Source `in-progress` and PR open → valid. Keep, update `last_ci`.
3. Source `in-progress` but NOT in tracking → check GitHub for open PR. If found, add to tracking. If not, reset unit to `pending`.
4. Write reconciled file.

**Tracking structure:**
```json
{
  "meta": {"tag": "<tag>", "wave": 1, "repo": "<owner>/<repo>", "flaky_checks": ["E2E"]},
  "tasks": {
    "task-<id>": {
      "pr": 123,
      "status": "working|review_clear|merged",
      "model": "sonnet|opus",
      "wave": 1,
      "last_ci": "passing|failing|unstable|pending"
    }
  }
}
```

**CRUD operations:**
```bash
# Add/update task
jq --arg task "task-<id>" --argjson pr <number> --arg model "sonnet" --argjson wave 1 \
  '.tasks[$task] = {"pr": $pr, "status": "working", "model": $model, "wave": $wave, "last_ci": "pending"}' \
  "$TRACK_FILE" > "$TRACK_FILE.tmp" && mv "$TRACK_FILE.tmp" "$TRACK_FILE"

# Update CI status
jq --arg task "task-<id>" --arg ci "passing" \
  '.tasks[$task].last_ci = $ci' "$TRACK_FILE" > "$TRACK_FILE.tmp" && mv "$TRACK_FILE.tmp" "$TRACK_FILE"

# Read all
jq -r '.tasks | to_entries[] | "\(.key) → PR #\(.value.pr) (\(.value.status), CI: \(.value.last_ci), wave \(.value.wave))"' "$TRACK_FILE"

# Remove after merge+cleanup
jq --arg task "task-<id>" '.tasks |= del(.[$task])' "$TRACK_FILE" > "$TRACK_FILE.tmp" \
  && mv "$TRACK_FILE.tmp" "$TRACK_FILE"
```
