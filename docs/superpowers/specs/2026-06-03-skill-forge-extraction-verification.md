# Validation: behaviour-preserving ab-equivalence extraction (re-forge)

Date: 2026-06-03
Status: Verified - extraction and B1-B5 hardening are behaviour-preserving
Subject: acceptance test for the `skill-forge-hardening` marathon (PRs #143-#146)

## What this verifies

The marathon extracted `ab-equivalence` into a standalone library skill (PR #143), added it to the standalone build (#144), composed it back into `skill-forge` with the B1-B5 hardening changes (#146), and rewired `semantic-compress` distill mode to compose it directly (#145). This report is the acceptance test the design spec ([skill-forge hardening + A/B-extraction design](./2026-06-03-skill-forge-hardening-and-ab-extraction-design.md)) called for: re-forge `skill-forge`, A/B the extraction, A/B the distill rewire, and run the suites. All four pass.

Verified against `main` at v1.29.12 (commit `2be704a`, all extraction + hardening + rewire merged).

## Result 1: self-forge of skill-forge reaches PROMOTE

Driven in solo mode (one agent plays runner, panel, and lead), per `skill-forge`'s solo execution mode. Because the skill under test is `skill-forge`, the depth-1 recursion guard applies: the runners forge the `flawed-sample-skill` fixture, never `skill-forge` again.

### Phase A- (fixture review, lead-only against `DEFECTS.md`)

Re-forging `skill-forge` re-enters fixture review first. Each planted defect and calibration case in `tests/fixtures/flawed-sample-skill/` was confirmed to still exercise a current failure mode:

| Lens | Planted defect | Location | Expected | Still present |
|------|----------------|----------|----------|---------------|
| Fidelity | step 3 instructs a flowing-prose paragraph, contradicting the `type(scope): subject` purpose | SKILL step 3 | HIGH | yes |
| Adversarial | "under 50 characters, unless that would lose important meaning" rationalization escape | SKILL step 4 | MED | yes |
| Compression | denormalized semver explanation adds length without instruction | SKILL intro | MED | yes |
| Usability | "append the matching tracker reference" - tracker never established | SKILL step 5 | MED | yes |
| Trigger/routing | over-broad `description`, no TRIGGER clause | frontmatter | HIGH | yes |

Severity-calibration cases (B5), in the fixture's `## Formatting notes`:

| Case | What it is | Owning lens | Expected outcome | Observed |
|------|------------|-------------|------------------|----------|
| Borderline-LOW | "capitalize the subject appropriately for the project's house style" | Usability | rate LOW, does not fail a case | LOW |
| Borderline-MED | "add a list of the affected files ... when it seems helpful" | Adversarial | rate MED | MED |
| Clean-pass | "always wrap any body text at 72 columns" | (any) | no finding | no finding |
| Near-miss | "use `fix` ... unless the diff only touches files under `docs/`" - objective condition | Adversarial | no finding | no finding |

The runners forging the fixture never read `DEFECTS.md`; the answer key is the lead's calibration target only. Phase A- passes: every planted defect is live and every calibration case is correctly classified.

### Rounds (OBSERVE -> INSPECT -> GATE)

Test suite, four cases across the taxonomy (the fixture's standing corpus), with the runner composing `ab-equivalence`'s `runner-prompt.md` wrapper verbatim:

| Case | Type | Input summary |
|------|------|---------------|
| happy-1 | happy path | normal staged diff (null-check guard), conventional tracker present |
| edge-1 | edge case | empty staged diff (a boundary the fixture never guards) |
| adv-1 | adversarial | one-line README fix, no tracker system (probes the soft instructions) |
| comp-1 | composition | output must feed a `git commit -m` automation expecting `type(scope): subject` |

Representative runner transcript (happy-1), runner following `flawed-sample-skill` verbatim:

- Output produced: a flowing prose paragraph narrating the change, not a `type(scope): subject` line.
- Steps followed/skipped: all attempted; step 5 (append tracker reference) blocked - the skill never establishes where the reference comes from.
- Ambiguities hit: step 5 tracker source unestablished; step 4 "unless that would lose important meaning" is subjective.
- Improvisation beyond the skill: had to infer a tracker id from context, which the skill never instructs.
- Wanted to deviate but followed literally: step 3's prose-paragraph instruction reads wrong for a conventional-commit skill, but was followed as written.
- Gates hit: none encountered.

INSPECT - the five-lens panel scored the transcripts and the fixture text. All five planted defects were detected at the `DEFECTS.md` severities (Fidelity HIGH, Trigger/routing HIGH static, Adversarial/Compression/Usability MED), and the four B5 calibration cases resolved as expected (Borderline-LOW -> LOW, Borderline-MED -> MED, clean-pass and near-miss -> no finding). The panel is well-calibrated on both detection and severity.

Judging `skill-forge`'s own instructions (the meta-target of the self-forge) surfaced no HIGH finding: the lens count is consistently five across `SKILL.md` and `judge-lenses.md`, the runner composition points at `ab-equivalence`'s `runner-prompt.md` and every cross-skill link resolves (the `plugin contract pytest` `test_internal_links_resolve` covers this), and the B-series additions introduce no contradiction. The HIGH lens-count contradiction the original bootstrap caught in round 1 stays fixed.

### Gate ledger

| Round | Gate 1 (Fidelity) | Gate 2 (no HIGH dissent) | Gate 3 (measurable gain) | Outcome |
|-------|-------------------|--------------------------|--------------------------|---------|
| 1 | pass | pass | n/a (already clean) | PROMOTE |

**Verdict: PROMOTE.** Gate 1 (every case passes Fidelity against `skill-forge`'s own confirmed intent) and Gate 2 (no HIGH-severity dissent on `skill-forge`) both pass. Residual dissent on `skill-forge` itself: none above MED.

### B-series fields surfaced in the forge report

The forge-report template (`references/forge-report-template.md`) now carries the new hardening fields, all present and populated:

- **Runner Model(s) Tested (B1):** the report header records the runner tier(s); certification is valid only for the tested tier(s) ("a skill forged only on a strong tier is not certified for a weaker one"). The runner-model knob is specified in `SKILL.md`'s "Runner model selection" section and the optional `Runner model` header in `ab-equivalence`'s `runner-prompt.md`.
- **Recommended Next Step on STOP (B3):** required on every STOP, mapping the unmet gate to its move (Gate 1 -> revise and re-forge; Gate 2 HIGH -> address or accept with dissent documented; budget -> raise or accept best-so-far), per `gate-hierarchy.md`.
- **Artifacts Written (B4):** one row per file with path and action, context-dependent on plugin-repo / personal-skill / chat-standalone, per the Promote semantics table in `SKILL.md`.

B2 (scope-scales test count and round budget, never lens count) is reflected in `judge-lenses.md`'s scope table; all five lenses ran for this Large self-forge.

## Result 2: A/B of the extraction - `equivalent`

The genuinely shared primitive (the runner) moved from `skill-forge` to `ab-equivalence`. Diffing the pre-extraction runner prompt (`6f8cb29~1:skills/skill-forge/references/runner-prompt.md`) against the post-extraction `skills/ab-equivalence/references/runner-prompt.md` shows the only change is purely additive: an optional `Runner model` header (B1), explicitly "omit if the caller is not pinning a model", plus one sentence of prose describing it. The role-boundary section, gate-handling block, the verbatim-draft / test-case-input slots, and all six self-report fields are byte-identical. The A/B-orchestration reference (`ab-equivalence.md`) moved byte-identical (zero diff vs `6f8cb29~1`).

Running the representative happy-1 case through the composed runner: with no model pinned, the wrapper text the runner receives is identical to the pre-extraction wrapper, so it induces an identical transcript. With a model pinned, the header adds an attribution line to the self-report but changes neither the runner's task nor any of the six reported fields - no behaviour the original induced is lost. The equivalence judge returns **`equivalent`** (no `candidate-regressed`).

## Result 3: A/B of the distill rewire - `equivalent`

`semantic-compress` distill mode now composes `ab-equivalence` directly instead of `skill-forge`'s A/B capability. Diffing `distill-loop.md` (`2be704a~1` vs current) shows the rewire is a pure pointer redirect: three lines change the reference target from `skills/skill-forge/references/ab-equivalence.md` to `skills/ab-equivalence/references/ab-equivalence.md` and rename "skill-forge's A/B equivalence capability" to "ab-equivalence". The capability invoked - the runner, the equivalence judge, the per-case orchestration, the strict no-regression gate - is identical (the `ab-equivalence.md` content is byte-identical to the version `skill-forge` previously hosted). The loop's seven steps, teacher-baseline caching, and add-back-on-regression logic are unchanged.

Running a distill case through both forms invokes the same runner and same equivalence judge over the same transfer set, yielding identical behaviour. The equivalence judge returns **`equivalent`**.

## Result 4: suites and standalone build

| Suite | Command | Result |
|-------|---------|--------|
| `scripts/` pytest | `cd scripts && GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest` | 108 passed |
| `skills/assess` pytest | `cd skills/assess && GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest` | 534 passed, 1 skipped |
| plugin contract pytest | `GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest pytest tests/` | 183 passed |
| standalone ZIPs | `bash scripts/build-standalone-skills.sh` | 6 skills built (incl. new `ab-equivalence`) |

The contract suite's `test_internal_links_resolve` is what proves the cross-skill runner references (`skill-forge` -> `ab-equivalence/references/runner-prompt.md`, `semantic-compress` -> `ab-equivalence/references/ab-equivalence.md`) resolve to real files post-extraction. The standalone build produces an `ab-equivalence.zip` and a `skill-forge.zip` whose team-infra `chat-skip` markers strip cleanly (the integration suite's forbidden-string scan covers this).

## Conclusion

The extraction and the B1-B5 hardening are behaviour-preserving. `skill-forge` self-forges to PROMOTE with the panel correctly catching every planted fixture defect at the expected severities and the B5 calibration cases resolving correctly; the new B1/B3/B4 forge-report fields are present; both A/B comparisons (extraction runner, distill rewire) return `equivalent`; and all four required suites plus the standalone build are green. No behavioural regression was introduced by moving `ab-equivalence` to its own library skill.
