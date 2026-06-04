# Design: Address /assess feedback - seam map, lying-map fix, executable architecture

_2026-06-04. Source: the v1.36.0 `/assess` self-assessment (score 7.0 / 8, AI-Native)._

## Context

Running `/assess` against this repo (the tool assessing itself) surfaced a Top 3
Actions list and four further opportunities. This design takes the **quick wins
plus the L4 architecture test**, and **deliberately excludes** the coverage /
mutation gate (Action 3): the repo's `CLAUDE.md` lists "No coverage gates" as an
intentional non-goal, so the tool is flagging a conscious design decision, not a
defect. Reversing that is out of scope for this round.

The work splits into two sequential PRs - docs first (fast, zero-risk), then the
enforcement changes (which carry small but real "might surface latent work"
risk, now measured and bounded).

## Goals

1. Clear the one high-confidence `lying_map` the run flagged, by making the doc
   it points at actually current.
2. Make the assess subsystem's genuine co-change cohesion **owned and documented**
   (the `hidden_coupling` attention list) - named as cohesion, not entanglement.
3. Make the layering `CLAUDE.md` already *states* into an **executable, merge-gating
   contract** (the L4 gap), and extend the L2 type gate onto the most-churned file.

## Non-goals

- No coverage gate, no mutation testing (honors the stated non-goal).
- No L0 wayfinding polish (cross-reference wiring, orphan linking) - deferred.
- No refactor of any hotspot file. The hidden_coupling is expected cohesion; the
  response is documentation, not decomposition.

## Measured premises (verified 2026-06-04)

- **Import boundary already holds:** no `skills/assess/scripts/lib/*.py` module
  imports an orchestrator. The archtest pins the current good state; it does not
  force a fix. (Verified by grep for orchestrator imports in `lib/`.)
- **mypy on `assess_core.py` = 4 errors:** one `Optional[str]` passed where `str`
  is expected (line ~580), three dict-union args passed to functions expecting a
  plain `dict` (lines ~768-771). Small, real, fixable by narrowing types.
- **lying_map detail:** `skills/assess/scripts/lib/README.md`, confidence `high`,
  `nearest-ancestor` association, subject churn 58 vs doc churn 1, last changed 2
  days ago (so most subject churn predates it; ~2 `lib/` commits postdate it).

## PR1 - `docs`: keep the assess seam map honest

**Files:** `skills/assess/scripts/lib/README.md`, `CLAUDE.md`, the spec doc.

1. **Re-anchor `lib/README.md` (Action 2, lying_map).** Walk the `lib/` commits
   since the README was written (`git log 46c308e..HEAD -- skills/assess/scripts/lib/`),
   reconcile each module's documented responsibility against the current code, and
   update any drift. The point is content currency, not a no-op touch - the doc
   should describe the modules as they are now.
2. **Extend the co-change seam map (Action 1, hidden_coupling).** Add a short
   "Co-change seams" section to `lib/README.md` naming the directories that evolve
   together by design - the `lib/` modules and their `tests/`, and the
   `scripts/` standalone-build ↔ `skills/` packaging seam - and stating plainly
   that this is genuine subsystem cohesion (the scanners, their tests, and the
   build that packages them move in lockstep), *not* accidental entanglement. Add a
   one-line pointer from `CLAUDE.md`'s `/assess architecture` section to that
   "Co-change seams" note so it is discoverable from the entry doc.

**Version:** PATCH (1.36.1). No test or output change. **Validation:** the README
content matches the current `lib/` modules; the markdown link contract stays green.

## PR2 - `feat`: make the architecture executable + widen the type gate

**Files:** new `skills/assess/tests/test_self_architecture.py`,
`skills/assess/pyproject.toml`, `skills/assess/scripts/assess_core.py`, `CLAUDE.md`.

1. **`test_self_architecture.py` - import-boundary contract.** An `ast`-based scan
   over `skills/assess/scripts/lib/*.py` that parses each module's `import` /
   `from ... import` statements and asserts none names an orchestrator module
   (`assess_core`, `assess_finalize`, `assess_report`, `assess_gate`,
   `complexity-treemap`, `doc-graph-svg`). Dependencies point inward: the
   deterministic core stays independent of the scripts that orchestrate it. Pure
   stdlib (`ast`, `pathlib`), no new dependency. It runs inside the required
   `skills/assess pytest` check, so a future upward import fails merge. This makes
   `CLAUDE.md`'s stated "core does data work, orchestrator coordinates" layering an
   executable contract (L4 Partial -> contract).
   - The test enumerates the orchestrator set explicitly (a small, named list) so
     a new orchestrator script is a conscious one-line addition, and a `lib`
     module importing one is a loud failure.
2. **Widen the mypy gate (L2 ratchet).** Add `scripts/assess_core.py` to the
   mypy `files` in `skills/assess/pyproject.toml`, and fix the 4 surfaced errors by
   narrowing types (guard the `Optional[str]` before the `LogEntry` call; tighten
   the dict-union types flowing into `integrate(...)`). No behaviour change.
3. **Document the contract.** Add a short note to `CLAUDE.md`'s `/assess
   architecture` section that the inward-only import boundary is now enforced by
   `test_self_architecture.py`, so the layering is a contract, not a convention.

**Version:** PATCH (1.36.2). Internal enforcement + types only; no `/assess` output
change. **Validation:** the new test passes (boundary holds today); `ruff + mypy
gates` job green with the widened scope; full `skills/assess` suite green.

## Sequencing

PR1 merges first (docs, zero-risk), then PR2 branches off the updated `main`.
Both touch `CLAUDE.md`; doing them sequentially avoids a divergent-version /
overlapping-edit conflict on that file. Each PR bumps `plugin.json` (hot file).

## Testing strategy

- PR1: no code; rely on the `plugin contract` link resolver staying green and a
  read-through that the README matches the current `lib/` modules.
- PR2: the new `test_self_architecture.py` is itself the test; plus the existing
  `skills/assess` suite and the `ruff + mypy gates` job must stay green with the
  widened mypy scope. Run locally with `GIT_CONFIG_GLOBAL=/dev/null`.
