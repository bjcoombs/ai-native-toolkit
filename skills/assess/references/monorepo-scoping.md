# Monorepo scoping: `/assess <path>`

When the user runs `/assess <path>` (a directory under the repo root), scope the
whole assessment to that subtree. The metrics, score, badge, wiki, and gate are
all computed for - and labelled with - the scope, and every artifact lands under
`.assess/<scope-slug>/` instead of `.assess/`. A root-level run (no path) is
unchanged.

This exists because a monorepo holds several services in one git repo. A
whole-repo score averages them into a number that describes none of them; a
scoped run answers "how ready is *this* service" with no signal bleeding in from
a sibling directory.

## Deriving the scope

`$SCOPE` is the path argument, resolved to a directory under `$REPO_ROOT`. If it
does not exist or is not under the repo root, the scripts exit non-zero with an
`error:` message - report that to the user rather than pressing on.

```bash
# $SCOPE is repo-relative, e.g. services/api
SCOPE_SLUG=$(printf '%s' "$SCOPE" | tr '/\\' '-')   # services/api -> services-api
ASSESS_DIR="$REPO_ROOT/.assess/$SCOPE_SLUG"
mkdir -p "$ASSESS_DIR"
```

Every `.assess/...` path in the orchestrator's Step 1-7.5 becomes
`$ASSESS_DIR/...` for a scoped run (the run-context, the SVGs, the stats
sidecars, the wiki, the badge, `finalize-input.json`). A whole-repo run keeps
`ASSESS_DIR="$REPO_ROOT/.assess"` so its output is byte-identical to before.

## Threading the scope into the scans

Each deterministic step takes the scope so it scores only the subtree while still
rooting at the repo (so `.assess/config.toml` excludes and the git churn window
are resolved once, repo-wide, and stay comparable across scopes):

<!-- chat-skip:start -->
```bash
# Heatmap - scores only files under the scope; title + default SVG name carry it
uv run "$SKILL_DIR/scripts/complexity-treemap.py" "$REPO_ROOT" \
  --scope "$SCOPE" -o "$ASSESS_DIR/complexity-heatmap.svg" \
  --stats "$ASSESS_DIR/complexity-stats.json"

# Deterministic core - reads the scoped stats sidecar it just wrote, confines the
# doc graph, doc staleness, dead-code, promissory-marker and change-coupling
# scans to the subtree, routes every artifact under .assess/<slug>/, and records
# `scope` / `scope_slug` in run-context.json for the report and badge.
uv run "$SKILL_DIR/scripts/assess_core.py" "$REPO_ROOT" --scope "$SCOPE"
```
<!-- chat-skip:end -->

The deterministic core confines the doc graph, doc staleness, dead-code,
promissory-marker and change-coupling scans to the subtree, routes every
artifact under `.assess/<slug>/`, and records `scope` / `scope_slug` in
`run-context.json` for the report and badge.

The doc-graph SVG has no `--scope` flag of its own; render it for the subtree by
pointing it at the scope directory as its root
(`doc-graph-svg.py "$REPO_ROOT/$SCOPE" -o "$ASSESS_DIR/doc-graph.svg"`), or skip
it for a code-only service.

The opt-in mutation re-run must be given the same scope so it finds the scoped
run-context: `assess_core.py "$REPO_ROOT" --opt-in-mutation --scope "$SCOPE"`.

## What "scoped" means per signal

- **Complexity / hotspots** - only files under the scope are scored; the
  dominance warning is per-scope.
- **Doc graph + staleness** - only docs (and `.base` hubs) under the scope.
- **Git churn** - the treemap saturation axis and the behaviour co-change pairs
  count only commits that touched the subtree.
- **Dead code, promissory markers, change coupling** - candidates/markers/pairs
  outside the scope are dropped, so a sibling's TODO or unused function never
  appears.
- **Observability** stays repo-level - telemetry is a whole-repo property, not a
  per-subtree one.

## Labelling

The badge label reads `AI-readiness (services/api)` and the wiki `index.md`
carries a `_Scope: \`services/api\`_` line, so a committed artifact is never
mistaken for a whole-repo score. The report should name the scope in its
heading too. `run-context.json` carries `scope` (repo-relative) and `scope_slug`
for any consumer; both are `null` on a whole-repo run.
