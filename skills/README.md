# Skills

The plugin's skills, auto-discovered by Claude Code from each `<name>/SKILL.md`. Each `SKILL.md` is its subtree's base doc and carries the frontmatter contract (`name`, `description` with a `TRIGGER` clause) documented in [`CLAUDE.md`](../CLAUDE.md). Bundled executables live under `<name>/scripts/`, reference docs under `<name>/references/`. Back to the [Map of Content](../docs/index.md).

## Portable

Work in any Claude Code session, and are also distributed as standalone ZIPs for Claude Desktop and claude.ai web.

| Skill | Base doc | Description |
|-------|----------|-------------|
| `/assess` | [`assess/SKILL.md`](./assess/SKILL.md) | Layered AI-readiness assessment (0-8 contract model) plus a complexity hotspot SVG and a doc-navigability graph SVG |
| `/huddle` | [`huddle/SKILL.md`](./huddle/SKILL.md) | Multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing |
| `/deslop` | [`deslop/SKILL.md`](./deslop/SKILL.md) | Detect and remove the telltale signs of AI writing; ships a [`references/full-checklist.md`](./deslop/references/full-checklist.md) |
| `/ghsync` | [`ghsync/SKILL.md`](./ghsync/SKILL.md) | Bulk-clone and fast-forward sync every GitHub repo you can access across an org |
| `/skill-forge` | [`skill-forge/SKILL.md`](./skill-forge/SKILL.md) | Harden a skill through judge-panel refinement rounds to a 3-tier promotion gate; refined through its own process |
| `/semantic-compress` | [`semantic-compress/SKILL.md`](./semantic-compress/SKILL.md) | Optimize LLM-directed instructions while preserving behaviour, with two transforms in one family - both gated on `/skill-forge`'s A/B equivalence harness. **compress** (Transform #1): a local core->pointer pass + an A/B-validated distill loop producing the smallest behaviourally-equivalent document; point at core knowledge the model holds, keep project-specific detail verbatim. **directive-clarity** (Transform #2): rewrites latent-action instructions into directives that name the action, gated on a measured directness gain at zero regression. Forged by `/skill-forge` |

## Assessment helpers

`/assess` is split into an orchestrator plus two render-time helper skills:

| Skill | Base doc | Description |
|-------|----------|-------------|
| `assess-findings` | [`assess-findings/SKILL.md`](./assess-findings/SKILL.md) | Renders the report from the deterministic `run-context.json` and the layer scorecard |
| `assess-pr` | [`assess-pr/SKILL.md`](./assess-pr/SKILL.md) | The end-of-run offers - open a PR, track the Top 3 Actions, freeze a CI gate |

## Team-orchestration library skills

Invoked by the workflow commands ([`/tm`](../commands/tm.md), [`/issues`](../commands/issues.md), [`/fix-pr`](../commands/fix-pr.md), [`/fix-develop`](../commands/fix-develop.md)), never standalone. Excluded from the standalone-ZIP build.

| Skill | Base doc | Description |
|-------|----------|-------------|
| `marathon` | [`marathon/SKILL.md`](./marathon/SKILL.md) | Parallel agent marathon orchestration: DAG analysis, waves, crash recovery, retrospective |
| `pr-review-merge` | [`pr-review-merge/SKILL.md`](./pr-review-merge/SKILL.md) | The PR review-to-green loop plus smart merge |
| `ab-equivalence` | [`ab-equivalence/SKILL.md`](./ab-equivalence/SKILL.md) | A/B behavioural equivalence testing - given two document versions and a transfer set, judges per-case equivalence. Composed by skill-forge and semantic-compress. |

## Acceptance Contract Verification

Marathon certifies *process* (tasks closed, CI green, PRs merged); none of that proves the assembled deliverable *works*. The acceptance contract closes that gap: a frozen, executable statement of what "done" observably means, authored before decomposition and executed at exit by a cold, non-implementing agent. The constitution lives in [`../FLOOR.md`](../FLOOR.md); the contract-file format and canary ground truth in [`../tests/canaries/README.md`](../tests/canaries/README.md); this section is the map.

### When a contract is required

Every marathon run started by [`/tm`](../commands/tm.md) or [`/issues`](../commands/issues.md), source-agnostic. There are exactly **two doors** and no third:

- **Frozen contract** - a contract authored, kill-tested, and frozen before decomposition. The only path that can certify `PASS`.
- **Operator-signed skip** - `operator_signoff` recorded before the run starts. Loud, human-authorized, and permanently capped at `UNVERIFIED`; it can never green. Skip is an amendment door too, so it is capped, not free.

`scripts/contract/start_gate.py <run-id>` fails closed (non-zero) unless one of the two exists; each command invokes it before decomposing. The run identifier is a Task Master tag for `/tm`, an issue-queue slug for `/issues`.

### Authoring a contract

A contract is markdown whose machine-readable criteria live in **exactly one fenced `yaml` block** (full format in [`../tests/canaries/README.md`](../tests/canaries/README.md)). The block has two keys: a contract-level `class` (`cli` | `interactive` | `report` | `refactor`) and a `criteria` list, each criterion `{id, tier, action, observation}` - `action` is what a cold agent *does*, `observation` is the binary, absence-resistant pass/fail it reads.

`tier` is a fact about the property, not a preference (observation ceiling): tier-1 = binary do-and-observe a cold agent fully verifies (hard gate); tier-2 = comparative/judged (reports only in v1, blocking unarmed - no calibration record is seeded); tier-3 = perceptual residue a cold agent structurally cannot observe (escalated to the operator, never dropped). Class sets the defaults - `refactor`->tier-2 equivalence, `cli`->tier-1, `report`->split (machine-resolvable citation locator is tier-1, faithfulness is tier-2), `interactive`->tier-3 human-mandatory. Downgrading a default needs written justification in the file; upgrading is free; proxy-promoting a tier-3/2 property to tier-1 is forbidden.

### Freeze before decomposition

Freezing records the file's sha256 in the completion record before the run decomposes. `scripts/contract/freeze.py` refuses unless the **red-contract-first kill-test** passes - every criterion executed against the class null artifact (empty repo, launch-only stub, empty document, or the original-plus-one-planted-divergence for refactor) must FAIL. A criterion that passes against absence is vacuous (the jet-fighters pathology) and kicked back to authoring. It also enforces **structural floors**: an `interactive` contract with zero tier-3 criteria cannot freeze - a contract cannot tier-1 away the human launch.

### Abort, not amend

The frozen contract is unamendable for the life of the run. If mid-run evidence shows the contract itself is wrong, the run **aborts to authoring** - re-author, re-kill-test, re-freeze - it is never edited in place. Aborting is the friction; there is no amendment door for motivated reasoning, and per the skip cap, no free skip door either. Aborts, escalations, and DEGRADED/PARTIAL/UNVERIFIED stamps are recorded as raw counts for the retro to read as evidence, not triggers.

### Cold-exit verification

Run-complete is redefined and gated in code. A fresh non-implementing agent executes the frozen contract against the assembled product; `scripts/contract/complete_gate.py <run-id>` fails closed unless `scripts/contract/validate_completion.py` accepts the record. Three properties hold it honest:

- **Custody by chokepoint.** Every verifier spawn goes through `scripts/contract/spawn_verifier.py`, whose interface accepts exactly two positional inputs (frozen-contract path, assembled-product path) and derives the run-id from the contract filename - leaking the implementation's seam vocabulary into the prompt is inexpressible. It mints a fresh token per run into a provenance side-channel only it writes; a record whose token is absent, forged, copied, or stale is stamped `DEGRADED-custody` and cannot certify. `test_custody.py` asserts by AST scan that this is the only code path writing verifier results into a record.
- **Couldn't-drive honesty.** The verifier lists every criterion it could not execute; a pass with undriven criteria is `PARTIAL`, never `PASS`.
- **Hash check.** The verifier re-hashes the contract; a mid-run edit (hash mismatch) aborts rather than certifying against a moved target.

### Relationship to marathon and pr-review-merge

The executable spine is `start_gate.py` -> chokepoint -> `validate_completion.py` -> `complete_gate.py`, each fail-closed. `marathon` invokes the start gate before decomposition and the chokepoint + complete gate at run-complete; `pr-review-merge` carries only a note that a green, merged PR is a process signal that never substitutes for these gates. All four marked files - [`marathon/SKILL.md`](./marathon/SKILL.md), [`pr-review-merge/SKILL.md`](./pr-review-merge/SKILL.md), [`../commands/tm.md`](../commands/tm.md), [`../commands/issues.md`](../commands/issues.md) - carry the literal `floor:cold-verify-completion` marker plus the gate invocation strings; `.github/workflows/floor.yml` (a required check) fails any PR that removes a marker or invocation from a file that previously carried it. The **retro boundary** excludes `FLOOR.md`, the markers, `floor.yml`, `scripts/contract/`, `scripts/canaries/`, and `tests/canaries/` from self-rewrite - the retro may propose changes to them, never self-apply.

### Honest narrowness

For the interactive/visual class that motivated this - the class where the machine tiers are thinnest - the machine checks are a backstop, not the load-bearing test. **Tier-3 human launch is.** The machinery's value there is that the launch becomes *required, recorded, and un-removable* rather than informal and skippable; the elaborate canary/blind/chokepoint apparatus carries the full load only for the CLI/report/refactor classes. Canary green is likewise necessary, never sufficient.

### Artifact paths

All paths relative to the repo root unless prefixed `.taskmaster/` (which lives at the Task Master container level).

| Artifact | Path |
|---|---|
| Acceptance contract (per run) | `.taskmaster/contract/<run-id>.contract.md` |
| Completion record (per run) | `.taskmaster/contract/<run-id>.completion.json` |
| Provenance side-channel (per run) | `.taskmaster/contract/<run-id>.provenance.json` (chokepoint-only) |
| Constitutional floor | `FLOOR.md` |
| Floor CI workflow | `.github/workflows/floor.yml` |
| Gates + schema + verifier | `scripts/contract/` (`start_gate.py`, `freeze.py`, `spawn_verifier.py`, `validate_completion.py`, `complete_gate.py`, `completion.schema.json`) |
| Canary harness + fixtures | `scripts/canaries/run_canaries.py`, `tests/canaries/` |
| Contract test modules | `tests/contract/` |
| External-anchor proof | `docs/floor-anchor-proof.md` |
