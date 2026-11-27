# Crash recovery

Crash Recovery from the marathon skill: what survives a crashed session and how to recover the marathon.

If the session crashes, background teammates live only as long as the session. The team config directory (`~/.claude/teams/session-<id>/`, named from the session ID - **not** the tag) is removed automatically when the session exits, and the task-list directory (`~/.claude/tasks/session-<id>/`) is intentionally kept so a resumed session recovers its tasks. There is no per-tag team directory to force-remove - the state that actually carries a marathon across a crash is the worktree and its `pr-tracking.json`, so recovery is just reconciliation:

```bash
# 1. Check worktrees for uncommitted work
git worktree list | grep "<tag>"

# 2. Re-invoke the calling command (e.g. /tm <tag> or /issues <label>) — reconciliation against the source of truth + GitHub handles stale tracking entries automatically.
```

Worktrees and `pr-tracking.json` survive crashes in `worktree/<tag>/`. If a background teammate pane genuinely lingers after the session exits, end it at the terminal level (`tmux ls` → `tmux kill-session`), not by deleting `~/.claude/` state.
