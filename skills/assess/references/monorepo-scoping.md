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
# <assess-skill-dir> is the directory SKILL.md's own `uv run` lines name. Do not
# assume a reference file gets the harness's path substitution.
# Heatmap - scores only files under the scope; title + default SVG name carry it
uv run "<assess-skill-dir>/scripts/complexity-treemap.py" "$REPO_ROOT" \
  --scope "$SCOPE" -o "$ASSESS_DIR/complexity-heatmap.svg" \
  --stats "$ASSESS_DIR/complexity-stats.json"

# Deterministic core - reads the scoped stats sidecar it just wrote, confines the
# doc graph, doc staleness, dead-code, promissory-marker and change-coupling
# scans to the subtree, routes every artifact under .assess/<slug>/, and records
# `scope` / `scope_slug` in run-context.json for the report and badge.
uv run "<assess-skill-dir>/scripts/assess_core.py" "$REPO_ROOT" --scope "$SCOPE"
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

## Headless and fleet runs

`assess_signals.py --scope <path>` runs the headless signals against one folder, as
`/assess <path>` does: the scans that read the scope read that folder, and repository-wide
scans read the whole checkout. The document's stamp records the folder as `scope`;
counts-only output writes `set` in its place, or its salted hash under `--hash-paths`, so a
scoped run never reads as a whole-repo run (`null`).

`assess_signals.py --units` adds the `units` and `unit_readings` blocks below, one scoped
pass per unit. It does not combine with `--scope`. `fleet.py --monorepo-units` passes it to
every repository and writes each unit's Step, next blocking gate and per-tag tally to
`fleet-data.json` (`unit_rows`). In counts-only output a unit's folder is dropped, or
written as its salted hash under `--hash-paths`, so a fleet file never names a unit.

## Units in a whole-repo run

A whole-repo run also reads each monorepo unit (the `units` block). `run-context.json`
`unit_readings` holds, per unit, the readings of the scans that see a different tree
from each unit (suppressions, doc staleness, import structure, file size, and the unit's
own coverage gate or instruction file), and tags every maturity-model condition
`unit` or `inherited`. A condition the unit does not read on its own, such as root CI or
review, is inherited from the root. A coverage gate or nested instruction file that sits
only in other units is theirs: a unit without its own reads none.

`unit_levels` holds each unit's Step, its next Step and what blocks it, and its tally
(practices met, measured, not measured and verified), from the same maturity model with
the unit's own readings in place. Inherited conditions count toward the unit's tally and
Step gates; `inherited` lists the met ones, which the report labels "inherited from root".
`unit_rollup` rolls the units up for the repository: the lowest unit's Step plus "N of M
measured units" at the next Step, never a mean and never a unit named. An unmeasured unit
counts toward the units, never as Step 1. `unlocks` ranks the Step gates that hold units
back by the units one change moves to their next Step. A change at the root to a
condition every held unit inherits counts once and ranks first on a tie; a unit-local
change counts once per unit. The repository's own Step and headline are unchanged. With
two or more units, the report shows the roll-up lines under the headline:

```text
Units: Step 1 (lowest unit) · 0 of 3 measured units at Step 2

Next unlock across units: an instruction file an agent reads is committed, once at the root, moves 3 units to their next Step
```

## Skills and plugin components as units

In a skills or plugin repository, such as a marketplace of many skills, each skill is a
unit. Each folder holding a `SKILL.md` is a unit of kind `skill`, and a plugin root's
`agents/` and `commands/` folders (beside its `.claude-plugin/plugin.json`) are units of
kind `agents` and `commands`. The shallowest folder wins, so a `SKILL.md` inside a skill,
such as a test fixture, is part of that skill. A skill with its own `pyproject.toml` or
`package.json` is one unit. A package above the skills is a unit of the source files no
skill holds, and no unit when the skills hold all of them.

Each skill gets its own readings, Step and next unlock, as any unit does. Its own evidence
is what sits in its folder: its `SKILL.md`, `references/`, scripts and tests, and an
instruction file inside it. Evidence outside the folder, such as root CI or a test under a
top-level `tests/`, reads `inherited` and is never credited to one skill. The roll-up ranks
the change that moves the most skills to their next Step. `[[units]]` in
`.assess/config.toml` still replaces discovery; a declared folder holding a `SKILL.md` is
of kind `skill`.
