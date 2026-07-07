# Uninstall `/assess` from a repo

Everything `/assess` leaves in a **target** repo, and how to remove it cleanly. Offered at the end of a run (see the assess-pr end-of-run offers); also runnable on demand. `/assess` writes only inside the target repo - it installs no global state - so removal is a bounded set of files plus a few in-file edits. Nothing here touches the `ai-native-toolkit` plugin itself; this removes the *artifacts a run produced*, not the tool.

Work from the target repo root (`$REPO_ROOT`). Do each step only if that artifact exists - a repo that never opted into the CI gate has no workflow to delete, and so on. None of these is destructive beyond the assessment: no source file, test, or history is touched.

## 1. Delete the `.assess/` directory

The assessment wiki and all transient artifacts live here: `assess-report.md`, `complexity-heatmap.svg`, `doc-graph.svg`, `run-context.json`, `complexity-stats.json` (+ `.prior.json`), `badge.json`, `actions.json`, `index.md`, `log.md`, `hotspots/`, `config.toml`, the `.cache/` scratch dir, and any decline markers (see step 4).

```bash
rm -rf "$REPO_ROOT/.assess"
```

If `.assess/` was committed, stage the deletion (`git rm -r --cached .assess` then commit) so it leaves the tree.

## 2. Remove the badge line from the README

The PR offer may have added a shields.io AI-readiness badge that points at `.assess/badge.json`. With `.assess/` gone the badge would 404, so remove the embed line. It looks like:

```markdown
![AI-readiness](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2F<owner>%2F<repo>%2F<default-branch>%2F.assess%2Fbadge.json)
```

Find and delete it (check `README.md`, and any other README the repo uses):

```bash
grep -rn "assess%2Fbadge.json\|.assess/badge.json" "$REPO_ROOT"/README* 2>/dev/null
```

Delete the matching line(s) with an editor - do not blind-`sed` a README, since surrounding prose may reference the badge.

## 3. Delete the CI gate workflow

If the freeze-into-CI offer was accepted, a gating workflow was written:

```bash
rm -f "$REPO_ROOT/.github/workflows/assess-gate.yml"
```

That is the only workflow `/assess` emits. Removing it stops the gate from running on future PRs. No other CI file is touched.

## 4. Remove decline markers

Permanent-decline markers (`.assess/.no-mutmut`, `.assess/.no-scc`, `.assess/.no-<tool>`) live inside `.assess/`, so step 1 already removed them. Only if the user chose to keep `.assess/` (e.g. to preserve the report) remove the markers explicitly so a future re-install starts from a clean slate:

```bash
rm -f "$REPO_ROOT"/.assess/.no-*
```

## 5. Remove archetype override markers from instruction files

`/assess` never writes these - a user adds an `assess-archetype: <value>` marker by hand to force the knowledge-base or software archetype. If one was added for `/assess`, remove it from the instruction file so it does not linger as a dangling directive. Scan the known instruction files:

```bash
grep -rn "assess-archetype" \
  "$REPO_ROOT/CLAUDE.md" "$REPO_ROOT/AGENTS.md" "$REPO_ROOT/GEMINI.md" \
  "$REPO_ROOT/.cursorrules" "$REPO_ROOT/.github/copilot-instructions.md" 2>/dev/null
```

Delete any matching line (typically `<!-- assess-archetype: knowledge-base -->`). Leave the rest of the instruction file untouched.

## 6. Optional: `.gitignore` hint and tracked findings

- If the PR offer's gitignore hint was followed, a `.gitignore` line was added for `.assess/complexity-stats.prior.json`. With `.assess/` gone it is inert; remove the line only if you want a spotless `.gitignore`.
- **Tracked findings are not removed.** Issues created by the "track the Top 3 Actions" offer (labelled `assess-finding`) are real work items in the user's tracker. Uninstalling the tool does not close them - the close decision is the user's. Mention any open `assess-finding` items so the user can triage them separately:

  ```bash
  gh issue list --label assess-finding --state open 2>/dev/null
  ```

## Verify

After the steps above, no assessment artifact should remain:

```bash
ls "$REPO_ROOT/.assess" 2>/dev/null            # should be absent
grep -rn "assess%2Fbadge.json" "$REPO_ROOT"/README* 2>/dev/null   # no output
ls "$REPO_ROOT/.github/workflows/assess-gate.yml" 2>/dev/null     # should be absent
```

All three silent means the repo is back to its pre-`/assess` state (bar any `assess-finding` tracker items the user chose to keep).
