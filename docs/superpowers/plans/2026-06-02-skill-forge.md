# skill-forge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `skill-forge`, a portable toolkit skill that hardens a skill (draft or existing) through judge-panel refinement rounds until it clears a 3-tier promotion gate - and prove it by having it forge itself.

**Architecture:** Pure-markdown orchestration like `marathon`/`huddle`. A lead (Forge Master) chairs ephemeral runner teammates that exercise the draft via prompt-injection and a persistent judge panel (five lenses) that scores the transcripts; the lead amends one thing per round and applies the gate hierarchy. Three execution modes (team / phased / solo) degrade gracefully so it ships as a standalone ZIP. Built in bootstrap order: flawed fixture -> seed -> self-forge -> promote.

**Tech Stack:** Claude Code Skill markdown (frontmatter + `references/`), Agent Teams (`TeamCreate`/`SendMessage`) with phased-subagent and solo fallbacks, `chat-skip`/`chat-replace` standalone-build markers, `tests/test_plugin_contract.py` + `scripts/tests/` pytest gates, the `scripts/build-standalone-skills.sh` pipeline.

**Source of truth:** `docs/superpowers/specs/2026-06-02-skill-forge-design.md`. Every content requirement below traces to a section there; when in doubt, the spec wins.

**Conventions for this plan:**
- All paths are relative to the repo root `ai-native-toolkit-main/` (work happens in `worktree/skill-forge/`).
- Contract tests auto-discover every `skills/*/SKILL.md` - no per-skill test file is written for them; the gate is running the suite after a file lands.
- Run contract suite: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
- Run standalone suite: `cd scripts && uv run --with pytest pytest -v`
- Commit after every task. Bump `.claude-plugin/plugin.json` only in the final task (one MINOR bump for the whole skill: `1.24.4` -> `1.25.0`).

---

## File Structure

```text
skills/skill-forge/
  SKILL.md                              # orchestrator: roles, loop, gate, modes, bootstrap, guards
  references/
    judge-lenses.md                     # the 5 lens definitions + behavioural/static rule
    gate-hierarchy.md                   # 3-tier gate, Gate-1 Fidelity bar, Gate-3 gain rule, promotion
    test-taxonomy.md                    # happy/edge/adversarial/composition design guide + corpus
    runner-prompt.md                    # the pure-wrapper runner template (5 sections)
    panel-ledger.md                     # ledger schema (per-lens findings/severity/verdict/dissent)
    forge-report-template.md            # the shipped report format
    example-forge-report.md             # Phase B output: skill-forge forging itself (canonical example)
  tests/
    fixtures/
      flawed-sample-skill/
        SKILL.md                        # one planted defect per lens (5 defects)
        DEFECTS.md                      # answer key: defect -> lens that should catch it
```

Modified outside the skill dir (Phase C):
- `scripts/standalone_skill_config.py` - add a `skill-forge` `SKILLS` entry
- `scripts/tests/test_integration.py` - add a `TestSkillForge` class
- `skills/README.md` - add `skill-forge` to the Portable table
- `.claude-plugin/plugin.json` - version bump

---

## PHASE A-: The flawed fixture (built first - it is the system's first input)

### Task 1: Flawed-sample-skill fixture with one defect per lens

**Files:**
- Create: `skills/skill-forge/tests/fixtures/flawed-sample-skill/SKILL.md`
- Create: `skills/skill-forge/tests/fixtures/flawed-sample-skill/DEFECTS.md`

The fixture is a small, plausible-looking skill that is deliberately broken in exactly five ways - one defect per lens - so a correct panel catches all five and a lens that misses its planted defect proves the **panel** is broken (spec: "The fixture calibrates the panel, not just the skill").

- [ ] **Step 1: Write the fixture SKILL.md with five planted defects**

Pick a simple, real-seeming domain so the runner can actually execute it - e.g. a "commit-message-writer" skill. Plant exactly one defect per lens:

| Lens it targets | Planted defect |
|---|---|
| Fidelity | A step that instructs an action contradicting the skill's stated purpose (e.g. says "write a conventional-commit message" but step 3 tells the agent to write a freeform paragraph). |
| Adversarial | A rationalization escape: an instruction the agent can talk itself out of ("use TDD unless it seems unnecessary"). |
| Compression | A bloated paragraph of denormalized training knowledge (e.g. three sentences explaining what semver is) that adds length without instruction. |
| Usability | An ordering/ambiguity defect: step 4 references output from step 6, so a literal reader cannot follow it in order. |
| Trigger/routing | A `description` with no `TRIGGER` clause / over-broad wording that would fire on unrelated prompts. |

Frontmatter `name:` must be `flawed-sample-skill` to match the directory (the contract test enforces `name == dir`). NOTE: this is a fixture, not a shipped skill - confirm in Step 3 that the contract suite does not scan `tests/fixtures/` (it scans `skills/*/SKILL.md`, where `*` is a direct child of `skills/`, so a nested fixture is out of scope). If the suite *does* pick it up, the missing `TRIGGER` defect would fail `test_skill_has_trigger_clause` - in that case move the fixture under a non-discovered path and update the paths in this plan.

- [ ] **Step 2: Write DEFECTS.md - the answer key**

A table mapping each planted defect to the lens that must catch it, plus the severity each should be rated. This is what Phase B checks the panel against. Example row format:

```markdown
| Lens | Planted defect | Location | Expected severity |
|------|----------------|----------|-------------------|
| Fidelity | step 3 contradicts stated purpose | SKILL.md step 3 | HIGH |
```

Include all five rows. Add a one-line header stating: "If a lens does not surface its row's defect during a forge, the panel - not the fixture - is at fault."

- [ ] **Step 3: Verify the fixture is not mistaken for a shipped skill**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Expected: PASS, and the test IDs do **not** include `flawed-sample-skill` (confirm the fixture is out of discovery scope). If it appears, relocate per Step 1's note and re-run.

- [ ] **Step 4: Commit**

```bash
git add skills/skill-forge/tests/fixtures/flawed-sample-skill/
git commit -m "feat: add skill-forge flawed-sample fixture (one defect per lens)"
```

---

## PHASE A: The seed (hand-authored well enough to run one full loop)

> Reference files are written before SKILL.md so SKILL.md's links resolve when the contract suite runs (`test_internal_links_resolve`).

### Task 2: references/judge-lenses.md

**Files:**
- Create: `skills/skill-forge/references/judge-lenses.md`

- [ ] **Step 1: Write the file**

Required content (spec: "The judge panel: five lenses"):
- Intro: the panel scales 2 -> 3 -> 5 lenses by stakes; confidence is the Gate-2 stopping decision, **not** a lens.
- A table with columns `Lens | Judges | Defect class it owns`, one row each for: **Fidelity**, **Adversarial** (state maintainability/future-edit-safety folds in here as an attack vector), **Compression** (denormalized training knowledge / bloat), **Usability**, **Trigger/routing**.
- A "Behavioural vs static evidence" section: four lenses judge runner transcripts (observed); Trigger/routing judges skill text (predictions). The forge report tags each finding `behavioural` or `static`; Trigger findings never gate Gate 1 and block only at Gate 2 as dissent; the fixture calibrates Trigger's *reading*, not behavioural observation.
- For each lens, a short "what it reads in the self-report" line tying to `runner-prompt.md` fields (Usability <- followed/skipped steps; Adversarial <- improvisation + "wanted to deviate").

Use inline code (`` `TRIGGER` ``) for illustrative file/clause mentions, not markdown links (CLAUDE.md rule; avoids dead-link test failures).

- [ ] **Step 2: Run contract suite**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Expected: PASS (no placeholder tokens, no leaked envelope tags).

- [ ] **Step 3: Commit**

```bash
git add skills/skill-forge/references/judge-lenses.md
git commit -m "feat: add skill-forge judge-lenses reference"
```

### Task 3: references/gate-hierarchy.md

**Files:**
- Create: `skills/skill-forge/references/gate-hierarchy.md`

- [ ] **Step 1: Write the file**

Required content (spec: "Gate hierarchy and promotion"):
- The 3-tier table `Gate | Bar | Effect`: Gate 1 Objective (every case passes Fidelity; hard), Gate 2 Panel confidence (all green + no HIGH-severity dissent; LOW/MED documented not blocking), Gate 3 Diminishing returns, Escape hatch Budget.
- **What "passes Fidelity" means** (Gate 1): pass = runner output preserves the intent's core propositions with no omission/distortion; sub-HIGH Fidelity findings advisory; a HIGH-severity Fidelity finding is an automatic Gate-1 failure; propositional, not numeric.
- **What "measurable gain" means** (Gate 3): the amendment's logged hypothesis is the yardstick - gain registers only if the metric the hypothesis targeted improved; coincidental improvement elsewhere does not count; regressions are handled upstream by Gate 1.
- **Promotion decision**: promote iff Gate 1 AND Gate 2 pass; otherwise STOP at Gate 3/budget with the best-so-far artifact and a report naming which gates were/weren't met. Dissent always documented, never suppressed.

- [ ] **Step 2: Run contract suite**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add skills/skill-forge/references/gate-hierarchy.md
git commit -m "feat: add skill-forge gate-hierarchy reference"
```

### Task 4: references/test-taxonomy.md

**Files:**
- Create: `skills/skill-forge/references/test-taxonomy.md`

- [ ] **Step 1: Write the file**

Required content (spec: "Test-case taxonomy"):
- The four case types with a design guide for each: **happy path**, **edge case** (boundary/unusual), **adversarial** (designed to make the agent rationalize out of following the skill), **composition** (skill combined with another skill or concept).
- Guidance: design 3-5 cases spanning the four types; when a new failure mode surfaces mid-run, add it to the corpus.
- **Persistent corpus**: the corpus is kept across re-forge runs so known failure modes re-run (compounding, like `.assess/`); state where it lives (decided in Task 9's Open-item resolution / Task 8 report - cross-reference, do not pre-decide a path here beyond "a sidecar in the target skill's forge directory").

- [ ] **Step 2: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS)
```bash
git add skills/skill-forge/references/test-taxonomy.md
git commit -m "feat: add skill-forge test-taxonomy reference"
```

### Task 5: references/runner-prompt.md

**Files:**
- Create: `skills/skill-forge/references/runner-prompt.md`

- [ ] **Step 1: Write the file**

Required content (spec: "The runner prompt"). It is the **pure-wrapper** template - never explains why the skill works or adds context. The five required sections, written as a copy-pasteable template the lead fills per runner:
1. Role statement: "You are a test runner. Apply the following skill to the following input, exactly as the skill instructs."
2. Role boundary: do not add/skip/reinterpret steps; note ambiguity but still follow as written; do not judge the skill (axis-1 boundary).
3. The skill draft (verbatim, fenced).
4. The test-case input.
5. Self-report format - required fields: output produced; steps followed / skipped + why; ambiguities hit + resolution; improvisation beyond the skill; any point it wanted to deviate but followed literally.

Add a closing note: these fields are required output (not optional), because Usability reads followed/skipped and Adversarial reads improvisation + "wanted to deviate".

- [ ] **Step 2: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS)
```bash
git add skills/skill-forge/references/runner-prompt.md
git commit -m "feat: add skill-forge runner-prompt reference"
```

### Task 6: references/panel-ledger.md

**Files:**
- Create: `skills/skill-forge/references/panel-ledger.md`

- [ ] **Step 1: Write the file**

Required content (spec: "The panel ledger"). Define the concrete JSON schema (adapt `marathon`'s `pr-tracking.json`). It is **one object** that both survives crashes and feeds non-team-mode judges. Show the schema with a worked example:

```json
{
  "meta": {"target_skill": "<name>", "round": 3, "mode": "phased", "budget": {"max_rounds": 8, "rounds_spent": 3}},
  "intent": [{"clause": "...", "status": "confirmed|assumed-rejected|assumed-accepted"}],
  "lenses": {
    "fidelity": {
      "round_verdict": "better|same|worse",
      "findings": [{"case": "edge-1", "severity": "HIGH", "summary": "...", "kind": "behavioural"}]
    }
  },
  "dissent": [{"lens": "adversarial", "severity": "MED", "summary": "...", "round": 2}],
  "amend_log": [{"round": 2, "change": "...", "hypothesis_metric": "...", "result": "improved|flat"}]
}
```

State explicitly: the `round_verdict` per lens is what Gate 3 reads; `dissent` with `severity: HIGH` is what blocks Gate 2; `intent[].status` enforces the ASSUMED guard (Fidelity ignores `assumed-rejected` clauses).

- [ ] **Step 2: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS)
```bash
git add skills/skill-forge/references/panel-ledger.md
git commit -m "feat: add skill-forge panel-ledger schema reference"
```

### Task 7: references/forge-report-template.md

**Files:**
- Create: `skills/skill-forge/references/forge-report-template.md`

- [ ] **Step 1: Write the file**

Required content (spec: "Artifacts"). A markdown template with sections: intent (with ASSUMED-clause acceptance record), the test suite, a per-round log (hypothesis -> change -> result), the gate ledger (which gate passed when), the severity-tagged dissent log, the final verdict (PROMOTE / STOP-best-so-far), and rounds + estimated waste. Every finding row carries its `behavioural`/`static` tag.

- [ ] **Step 2: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS)
```bash
git add skills/skill-forge/references/forge-report-template.md
git commit -m "feat: add skill-forge forge-report template"
```

### Task 8: SKILL.md seed (the orchestrator)

**Files:**
- Create: `skills/skill-forge/SKILL.md`

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: skill-forge
description: "Harden a skill (draft or existing) through judge-panel refinement rounds until it clears a 3-tier promotion gate, then promote it. A quality gate that runs after authoring, not an authoring tool. TRIGGER when the user types /skill-forge, asks to test/harden/forge/prove a skill, wants a skill driven through adversarial rounds before shipping, asks 'is this skill ready?', or wants a skill quality-gated by a judge panel."
---
```

(The `TRIGGER` clause satisfies `test_skill_has_trigger_clause`; `name` matches the dir.)

- [ ] **Step 2: Write the body**

Required sections, each pointing to its reference file via a resolvable relative link (these files now exist, so `test_internal_links_resolve` passes):
- **Boundary** (one line): prove-and-promote gate, not an authoring tool; pairs with authoring skills.
- **Input contract** + the **intent derivation guard** (ASSUMED clauses, user accepts/rejects before round 1).
- **Roles** (Forge Master / runners / judge panel) + the **role boundary** recursion guard (runners apply, lenses judge, lead amends).
- **The runner prompt** - link to `references/runner-prompt.md`.
- **The five lenses** - link to `references/judge-lenses.md`.
- **The loop** OBSERVE -> INSPECT -> GATE -> AMEND (with the mermaid diagram from the spec; EVALUATE is the regression-focused re-run, not a phase) + one-change-per-round with the Phase-B-tunable escape.
- **Gate hierarchy** - link to `references/gate-hierarchy.md`.
- **Execution modes** (team / phased / solo) + capability detection via `$CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`; link to `references/panel-ledger.md` for how non-team modes remember.
- **Test taxonomy** - link to `references/test-taxonomy.md`.
- **Self-application / bootstrap** (Phase A- -> A -> B -> C) + the **depth-1 recursion guard** (when the target IS skill-forge, runners forge the fixture, never skill-forge again).
- **Artifacts** - link to `references/forge-report-template.md`.

Wrap all Agent-Teams infrastructure (`TeamCreate`/`SendMessage`/`TeamDelete` blocks, the team-mode execution rows, any `$ARGUMENTS`/namespaced-command mentions) in `<!-- chat-skip:start -->` / `<!-- chat-skip:end -->`, and the execution-mode-detection line in a `<!-- chat-replace:KEY -->`, exactly as `huddle/SKILL.md` does. Keep markers balanced and at line start.

- [ ] **Step 3: Run contract suite**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Expected: PASS - `skill-forge` now appears in the parametrized IDs with valid frontmatter, a TRIGGER clause, resolving links, no placeholders, no leaked envelope tags.

- [ ] **Step 4: Commit**

```bash
git add skills/skill-forge/SKILL.md
git commit -m "feat: add skill-forge seed orchestrator (SKILL.md)"
```

---

## PHASE B: Self-forge (the acceptance test - skill-forge forges itself)

### Task 9: Run the self-forge to the gate

This is a **runtime activity**, not a code-TDD task: the seed is exercised against itself and amended until it clears its own gate. The amendments land back in the Phase A files. This is the skill's reason for existing - "the version that ships is the one its own panel signed off."

**Entry criteria:** Tasks 1-8 committed; contract suite green.

- [ ] **Step 1: Smoke-run one full loop**

Invoke `skill-forge` in this session with:
- target = `skills/skill-forge/SKILL.md` (the seed)
- intent = derived from the seed, each clause marked ASSUMED and explicitly accepted/rejected by the user before round 1 (exercises the intent guard)
- the runners' forge target = the `flawed-sample-skill` fixture (depth-1 guard: skill-forge forges the fixture, not itself)

Confirm one complete OBSERVE -> INSPECT -> GATE -> AMEND cycle executes and writes a panel ledger. If the seed cannot complete a loop, fix the seed (Phase A files) and recommit before continuing - this is expected bootstrap friction.

Expected: one ledger written, at least one round verdict per lens, at least one logged amendment hypothesis.

- [ ] **Step 2: Validate the panel against the fixture answer key**

Because the runners forge the `flawed-sample-skill`, the panel's findings must include all five planted defects from `DEFECTS.md`. For any planted defect a lens fails to surface, the **panel** is at fault: fix that lens's definition in `references/judge-lenses.md` and re-run. (Spec: fixture calibrates the panel.)

Expected: all five planted defects surfaced by their owning lenses at the expected severities.

- [ ] **Step 3: Iterate to the gate**

Run rounds until the gate hierarchy returns PROMOTE (Gate 1 + Gate 2 pass) or the budget ceiling stops with an honest report. One change per round; log each hypothesis and its result in the ledger. Apply each amendment to the relevant Phase A file and commit per round:

```bash
git add skills/skill-forge/
git commit -m "refine: skill-forge self-forge round N - <one-line hypothesis>"
```

**Acceptance:** skill-forge reaches PROMOTE on itself, OR stops at budget with a report that names the unmet gate and the residual HIGH-severity dissent. Either is a valid v1 outcome (spec: the loop always terminates with useful output); a budget-stop ships with the honest report and a follow-up note.

- [ ] **Step 4: Confirm contract suite still green after refinements**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Expected: PASS.

### Task 10: Ship the self-forge report as the canonical example

**Files:**
- Create: `skills/skill-forge/references/example-forge-report.md`

- [ ] **Step 1: Capture the Phase B forge report**

Write the actual report from Task 9 (filled from `forge-report-template.md`) into `example-forge-report.md`. It documents skill-forge finding and fixing its own weaknesses - the most honest possible example. Add a one-line header noting it is the real output of forging this skill, not a mock.

- [ ] **Step 2: Link it from SKILL.md**

Add a one-line "Example" pointer in `SKILL.md` linking to `references/example-forge-report.md` (resolvable link).

- [ ] **Step 3: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS)
```bash
git add skills/skill-forge/SKILL.md skills/skill-forge/references/example-forge-report.md
git commit -m "docs: ship skill-forge self-forge report as canonical example"
```

---

## PHASE C: Promote + ship

### Task 11: Standalone build config entry

**Files:**
- Modify: `scripts/standalone_skill_config.py` (add a `skill-forge` entry to `SKILLS`)

- [ ] **Step 1: Add the SKILLS entry**

Mirror the `huddle` entry's shape. Use:
- `standalone_name`: `"skill-forge"`
- `standalone_description`: the skill's description with the TRIGGER clause but **no** plugin namespacing (the `VERSION_SUFFIX` is appended automatically).
- `source_dir`: `"skills/skill-forge"`
- `exclude_dirs`: `{"tests", "__pycache__", ".pytest_cache", ".venv"}` (excludes the fixture dir from the ZIP)
- `replacements`: a dict for each `chat-replace:KEY` used in the skill (at minimum the execution-mode-detection line -> a solo-mode-only phrasing).

- [ ] **Step 2: Verify config imports and the team-exclusion test still passes**

Run: `cd scripts && uv run --with pytest pytest tests/test_transform.py -v && cd ..`
Run: `uv run --with pytest pytest tests/test_plugin_contract.py::test_team_skills_excluded_from_standalone -v`
Expected: PASS (adding `skill-forge` does not add `marathon`/`pr-review-merge`).

- [ ] **Step 3: Commit**

```bash
git add scripts/standalone_skill_config.py
git commit -m "feat: add skill-forge to standalone-skill build config"
```

### Task 12: Standalone integration test (test-first)

**Files:**
- Modify: `scripts/tests/test_integration.py` (add a `TestSkillForge` class)

- [ ] **Step 1: Write the failing test class**

Mirror `TestAssess`/`TestHuddle`. Add fixtures and assertions:

```python
class TestSkillForge:
    @pytest.fixture(scope="class")
    def forge_zip(self, tmp_path_factory):
        return _build("skill-forge", tmp_path_factory.mktemp("skill-forge"))

    def test_skill_md_present(self, forge_zip):
        assert "skill-forge/SKILL.md" in forge_zip.namelist()

    def test_fixture_tests_excluded(self, forge_zip):
        names = forge_zip.namelist()
        assert not any("/tests/" in n for n in names)

    def test_no_team_tool_envelope(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "TeamCreate" not in content, f"{name}: team infra leaked into standalone"
            assert "SendMessage" not in content, f"{name}: team infra leaked into standalone"

    def test_no_namespaced_slash_command(self, forge_zip):
        for name, content in _md_contents(forge_zip).items():
            assert "ai-native-toolkit:skill-forge" not in content

    def test_frontmatter_name_correct(self, forge_zip):
        skill_md = forge_zip.read("skill-forge/SKILL.md").decode("utf-8")
        assert "name: skill-forge" in skill_md
```

(Match the helper names `_build`, `_md_contents` already used in the file.)

- [ ] **Step 2: Run it to see it pass or fail**

Run: `cd scripts && uv run --with pytest pytest tests/test_integration.py::TestSkillForge -v && cd ..`
Expected: If `test_no_team_tool_envelope` FAILS, a `chat-skip` block is missing around team infra in `SKILL.md` - fix the markers in `skills/skill-forge/SKILL.md` and re-run until PASS. This test is the gate that proves the standalone degrade actually strips team infra.

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/test_integration.py skills/skill-forge/SKILL.md
git commit -m "test: add skill-forge standalone ZIP integration tests"
```

### Task 13: Register in the skills catalog

**Files:**
- Modify: `skills/README.md` (add `skill-forge` to the **Portable** table)

- [ ] **Step 1: Add the catalog row**

In the Portable table, add:
```markdown
| `/skill-forge` | [`skill-forge/SKILL.md`](./skill-forge/SKILL.md) | Harden a skill through judge-panel refinement rounds to a 3-tier promotion gate; refined through its own process |
```

- [ ] **Step 2: Run contract suite + commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v` (Expected: PASS - link resolves)
```bash
git add skills/README.md
git commit -m "docs: register skill-forge in the skills catalog"
```

### Task 14: Version bump

**Files:**
- Modify: `.claude-plugin/plugin.json` (`.version`: `1.24.4` -> `1.25.0`)

- [ ] **Step 1: Bump the version**

New skill = MINOR bump (CLAUDE.md versioning table). Set `.version` to `1.25.0`.

- [ ] **Step 2: Verify and commit**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py::test_plugin_json_valid -v` (Expected: PASS)
```bash
git add .claude-plugin/plugin.json
git commit -m "chore: bump plugin version to 1.25.0 for skill-forge"
```

### Task 15: Full gate + PR

- [ ] **Step 1: Run all gates**

Run: `uv run --with pytest pytest tests/test_plugin_contract.py -v`
Run: `cd scripts && uv run --with pytest pytest -v && cd ..`
Run: `bash scripts/build-standalone-skills.sh skill-forge` (confirm the ZIP builds)
Expected: all PASS; ZIP produced under `dist/standalone-skills/`.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin skill-forge
gh pr create --base main --title "feat: add skill-forge skill-hardening harness" \
  --body "Adds skill-forge, refined through its own self-forge process. See docs/superpowers/specs/2026-06-02-skill-forge-design.md and the canonical example forge report. Version bumped to 1.25.0."
```

- [ ] **Step 3: Shepherd to merge**

Drive CI to green and merge per the repo's Marathon Configuration (solo-maintainer, 0 approvals, lead merges at green required checks with `--admin` if the AI-readiness regression gate is mid-rerun). Use `/fix-pr` for the watch/iterate cycle.

---

## Self-Review

**Spec coverage** (each spec section -> task):
- Boundary, input contract, intent guard -> Task 8 (body) + Task 9 (exercised)
- Roles + role-boundary guard -> Task 8
- Runner prompt -> Task 5
- Five lenses + behavioural/static -> Task 2
- The loop + one-change-per-round + escape -> Task 8
- Gate hierarchy + Gate-1 bar + Gate-3 gain + promotion -> Task 3
- Execution modes + capability detection -> Task 8
- Panel ledger -> Task 6
- Test taxonomy + persistent corpus -> Task 4
- Self-application bootstrap + depth-1 guard -> Task 8 (documented) + Task 9 (executed)
- Flawed fixture, one defect per lens, calibration -> Task 1 + Task 9 Step 2
- Fixture versioned / re-enter Phase A- on re-forge -> documented in Task 8 bootstrap section
- Artifacts (report, corpus) + canonical example -> Task 7 + Task 10
- Crash recovery + retrospective (= panel ledger) -> Task 6
- Standalone ZIP + chat-skip/replace -> Tasks 8, 11, 12
- Decisions / versioning / catalog -> Tasks 13, 14

**Gaps found and resolved:** the spec's "Open questions for the plan" (ledger schema, artifact location, budget defaults) are resolved here - ledger schema in Task 6, artifact location as a sidecar in the target skill's forge dir (Task 4/7), budget defaults tuned during Task 9 and recorded in the example report.

**Placeholder scan:** content requirements are concrete per file; no "TBD/add error handling" steps. Phase B is intentionally a runtime protocol with explicit entry/acceptance criteria, not invented code.

**Type/name consistency:** ledger field names (`round_verdict`, `dissent[].severity`, `intent[].status`, `amend_log`) are used identically in Tasks 6, 3 (Gate 3 reads `round_verdict`), and 9. Lens names match across Tasks 1, 2, 9. File paths in the File Structure block match every task's `Create:`/`Modify:` line.
