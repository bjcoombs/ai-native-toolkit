# Scoring model: current state

This page is the single source for how `/assess` and `/fleet` score a repository. It records **current decisions only**. Each item links the issue or pull request that holds the full reasoning. When an issue body or an older comment disagrees with this page, this page is right and the issue is stale. To change the model, raise it on https://github.com/bjcoombs/ai-native-toolkit/issues/740 and update this page in the same change.

**How to read the status of an item.** Each item is marked **Shipped**, with the pull request that merged it, or **Decided, pending**, with the issue that will ship it. The linked issue is the authority on whether a pending item has landed since this page was last edited: when it closes, move the item to Shipped here.

## Glossary

| Term | Meaning | Status |
|---|---|---|
| **Condition** | One checkable practice, with file:line evidence. It is the unit of counting. Each level of a maturity-model row is one condition, `<row id>.<level>` (`lib/level_tally.py`). | Shipped, https://github.com/bjcoombs/ai-native-toolkit/pull/888 |
| **Input class and grade** | Two layers, both in `lib/level_signals.py`. Each signal has an **input class**: what its producer read. The seven classes are `config_text`, `head_text`, `head_structure`, `git_history`, `execution_artifact`, `ci_executed` and `platform`. The input class fixes a **grade**. `config_text` grades as `config_only`, `head_text` as `declared`, and `head_structure`, `git_history` and `execution_artifact` as `measured`. `ci_executed` and `platform` grade as `verified`. A condition takes the weakest grade of its criteria. `measured` and `verified` conditions count toward N; `config_only` and `declared` ones are listed, not counted. Earlier lists of five or four "signal classes" in https://github.com/bjcoombs/ai-native-toolkit/issues/783 mixed the two layers. | Shipped, https://github.com/bjcoombs/ai-native-toolkit/pull/802 and https://github.com/bjcoombs/ai-native-toolkit/pull/888 |
| **State** | Each condition has one state per run. **Met**: verified at this commit. **Unmet**: checked and absent, including a practice that was removed. **Not measured**: the run could not see it (headless, no network, shallow history), shown with its reason. **Not applicable**: nothing for it to act on, by a versioned rule. **Declined**: the repository declared an opt-out. | Met, unmet and not measured shipped in https://github.com/bjcoombs/ai-native-toolkit/pull/888. Not applicable and declined are reserved states that no rule produces yet: decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/827 and https://github.com/bjcoombs/ai-native-toolkit/issues/828 |
| **Requires** | A real dependency between two conditions, written on the level: a condition whose own criteria are met takes the state of the first required condition that is not met. For example, mutation credit requires CI running tests (`layer-6-coverage` level 2 requires `layer-5-ci.1`). | Shipped, https://github.com/bjcoombs/ai-native-toolkit/pull/888 |
| **Truth tag** | The truth a condition keeps honest. There are eight: `behaviour`, `contracts`, `docs`, `structure`, `agent_context`, `agent_runtime`, `governance`, `supply_chain`. Tags are mutual: no tag ranks above another, and they are not a ladder. Every condition has one or more tags. Tags replace layers as the organising frame for reports, `/fleet` roll-ups and advice. | Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/843. The shipped model carries no tags yet |
| **Layer (L0-L8)** | To become a legacy alias only, so old configs and fleet history resolve. New work groups by tag. | Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/843. The shipped model still groups conditions by layer row, and gates name a row and a level |
| **Step** | A milestone for how much agent autonomy the repository can support. See Steps below. | Shipped, https://github.com/bjcoombs/ai-native-toolkit/pull/888 |
| **Gate** | A condition a Step requires, whatever the count. Gates are named after their condition, not after a tag or a layer. Gates read state, never grade: a declared condition (an instruction file is text at HEAD) or a config-only one holds its gate while staying out of N. | Shipped, https://github.com/bjcoombs/ai-native-toolkit/pull/888 |
| **Hazard** | A harmful practice, often two good practices combined (for example, CI plus a label that skips it). A hazard blocks the Step whose gate it undermines. It never subtracts from N. | Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/828 |
| **Badge** | Do not use this word for a condition. In the report it means a state marker only (HAZARD, DECLINED, NOT MEASURED). | Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/844 |

## The headline

**Shipped** in https://github.com/bjcoombs/ai-native-toolkit/pull/894 (`lib/headline.py`):

```
Step 3 · Unattended agent PRs · contracts in place
Next: Step 4 · Autonomous loops unlocks when a coverage gate is enforced
Verified practices: 9 of 12 measured · 4 not measured (needs a full run: 1, no remote: 3) · model <version>
```

- **The Step and the next unlock lead.** A Step does not move when the rules add conditions, so these lines are stable across model versions. The Next line names the next Step and the one change that reaches it, blocking gates first.
- **The tally line.** N is the number of met, counted conditions. M is the number of counted conditions that were **measured** (met or unmet). Not-measured conditions are excluded from both N and M and shown beside the count with their reasons. An unknown never earns anything, so a blind run never raises N. A run that can see more, or less, can still change N and M, which is why the not-measured count and its reasons sit on the same line. When a new maturity-model version changes the conditions, the move is shown in the Recount, not as a drop: on the first `/fleet` run under the new version, stored scans are re-scored under it and each move the rule change alone caused is listed, saying the repository did not change (`lib/fleet_rescore.py`, `lib/fleet_credit.py`, shipped in https://github.com/bjcoombs/ai-native-toolkit/pull/803).
- **The inputs.** The headline is computed by the deterministic core from the run's `level` block and is never set by the LLM. That block depends on the commit, the maturity-model version, the committed config, and what this run could measure (for example, whether it had GitHub access). Two runs at one commit with the same access print the same headline. Changes are attributed by cause: repo, rules or scanner (https://github.com/bjcoombs/ai-native-toolkit/issues/771, https://github.com/bjcoombs/ai-native-toolkit/issues/774).
- **There is no "Level N", no 0-8 score, no mean and no band name.** The "X / 8" headline was retired in https://github.com/bjcoombs/ai-native-toolkit/pull/894. The `/fleet` roll-up shows, per unit and `team` row, the lowest level plus "N of M measured" (https://github.com/bjcoombs/ai-native-toolkit/issues/772). There N and M count **repositories**, not conditions: M is the unit's repositories and N those measured on that row, so an unknown or failed repository stays in M and is never read as 0 (`skills/fleet/references/fleet-data.md`, Roll-ups). It is a different number from the per-repository tally above. Diagrams that still average repository levels are open in https://github.com/bjcoombs/ai-native-toolkit/issues/806.

**Decided, pending** in https://github.com/bjcoombs/ai-native-toolkit/issues/844:

- **Rename the tally line to "Practices in place: N of M measured"** (maintainer decision of 2026-11-22). N counts both `measured` and `verified` conditions, so "Verified practices" claims more than N holds. There stays one tally, with the verified share inside it shown in the tag strip or beside the count. The change covers `lib/headline.py`, its badge summary and this page.
- **The per-tag strip** becomes a further line under the tally. A condition with several tags appears in each tag's cell, but N counts it once. It needs the tags from https://github.com/bjcoombs/ai-native-toolkit/issues/843.

## Steps

**Shipped** in https://github.com/bjcoombs/ai-native-toolkit/pull/888, as the `[[steps]]` table in `skills/assess/levels/default.toml`:

| Step | Name | What it supports | Gate added at this Step | Provisional threshold |
|---|---|---|---|---|
| 1 | `Assisted` | one supervised agent | none | 0 |
| 2 | `Supervised agent PRs` | agent PRs a person reviews | `instructions`: an instruction file an agent reads (`layer-0-instructions` level 1) | 1 |
| 3 | `Unattended agent PRs` | agent PRs without a person watching | `ci_runs_tests`: CI runs the tests, unmasked (`layer-5-ci` level 1, https://github.com/bjcoombs/ai-native-toolkit/issues/775) | 3 |
| 4 | `Autonomous loops` | marathon-style autonomous loops | `coverage_gate`: an enforced coverage gate (`layer-6-coverage` level 1) | 5 |

- Gates accumulate, so Step 4 needs all three.
- A Step also needs its count threshold of met, counted conditions. The thresholds above are provisional. They will be calibrated on the 12-repository corpus and versioned with the model (decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/842).
- Gates are staged because a 36-repository private fleet spike found an enforced coverage gate in none of the 36. Requiring every gate for every Step would hold all of them at Step 1 (https://github.com/bjcoombs/ai-native-toolkit/issues/744).
- Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/828: an active hazard blocks the Step whose gate it undermines, and a declined condition never satisfies a gate.

## What else is decided

- **Rules for new conditions.** Decided, pending.
  - A condition is accepted only if, across the 12-repository corpus, at least one repository meets it and at least one does not (https://github.com/bjcoombs/ai-native-toolkit/issues/842).
  - A condition with an empty denominator reads not measured, never met (https://github.com/bjcoombs/ai-native-toolkit/issues/827).
- **Name-only agent matches become hints.** Decided, pending in https://github.com/bjcoombs/ai-native-toolkit/issues/883 (maintainer decision of 2026-11-22). A built-in agent match with no `[bot]` suffix or verified app identity, such as `noreply@anthropic.com`, the `copilot` username or a GitLab username match, becomes a candidate that needs confirmation and is not counted until it is confirmed. `[bot]`-suffix and co-author-trailer rules stay counted.
- **Config-only signal renames.** Shipped in https://github.com/bjcoombs/ai-native-toolkit/pull/911, completing https://github.com/bjcoombs/ai-native-toolkit/issues/783: `ci_checks.test_or_lint_runs` is now `ci_checks.test_or_lint_configured`, and `coverage_gate.enforced` is now `coverage_gate.configured`. Pure renames go through `SIGNAL_ALIASES` in `lib/fleet_rescore.py`, so a re-score shows no Recount line. The classification shipped earlier in https://github.com/bjcoombs/ai-native-toolkit/pull/802.
- **Spread comes from interaction, guard and contradiction conditions, not from more file-presence checks.** The candidate conditions from the calibration are the children of https://github.com/bjcoombs/ai-native-toolkit/issues/826 (https://github.com/bjcoombs/ai-native-toolkit/issues/845 to https://github.com/bjcoombs/ai-native-toolkit/issues/858).
- **No general dependency graph.** Dependencies are `requires` between conditions plus the three gates.
- **Outcome claims** (do agents do better on repositories that meet more conditions?) are a separate research track. They are reported as "observed" and are never added to N.

## Superseded

| Earlier position | Replaced by | Where |
|---|---|---|
| A 0-8 score across layers | The open-ended count of counted conditions | https://github.com/bjcoombs/ai-native-toolkit/issues/740 huddle verdict |
| "Level N" as the headline, read/write/meta bands | The Step and next unlock, then "N of M measured", then the tag strip | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
| "Verified practices" as the tally label | "Practices in place" | https://github.com/bjcoombs/ai-native-toolkit/issues/844 decision of 2026-11-22 |
| Layers as the scoring frame | Truth tags; layers survive only as an alias | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
| Gates named L0, L5, L6, or by tag | Gates named after their conditions: `instructions`, `ci_runs_tests`, `coverage_gate` | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
| Step names "Parallel" and "Supervised autonomy" | Assisted, Supervised agent PRs, Unattended agent PRs, Autonomous loops | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
| "Never lose what you've earned", "highest Step held" | Dropped; achievements are dated events only | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
| Negatives lower the level | Hazards block Steps and never net | https://github.com/bjcoombs/ai-native-toolkit/issues/828 |
| M includes conditions that were not measured | M is measured conditions only | https://github.com/bjcoombs/ai-native-toolkit/issues/740 decision of 2026-11-21 |
