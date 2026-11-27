# Uninstalling `/assess` (Step 8)

Step 8 of `SKILL.md`, run when the user selects the uninstall offer.

When selected, read the full removal procedure and follow it. The path is in run-context:

```bash
jq -r '.uninstall_instructions_path' "$REPO_ROOT/.assess/run-context.json"
# -> references/uninstall.md (relative to the assess skill directory)
```

The value is always that path; if it is ever an object with `available: false`, use `references/uninstall.md` directly.

Read that `references/uninstall.md` and perform each step that applies (skip artifacts the run never created - no CI gate means no workflow to delete). Do each destructive action only after the user has confirmed uninstall, and **do not** close `assess-finding` tracker items (or the unlabelled upstream issues the upstream-issues flow filed) - surface them so the user decides. Report back which artifacts were removed and which (if any) were left for the user to handle. If the user prefers to do it themselves, hand them the steps from `references/uninstall.md` rather than performing them.
