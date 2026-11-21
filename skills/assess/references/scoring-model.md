# Scoring model: current state

This page is the single source for how `/assess` and `/fleet` will score a repository. It records **current decisions only**. Each section links the issue that holds the full reasoning. When an issue body or an older comment disagrees with this page, this page is right and the issue is stale. To change the model, raise it on https://github.com/bjcoombs/ai-native-toolkit/issues/740 and update this page in the same change.

Status as of 2026-11-21: decided, and **not yet implemented**. The shipped engine (`lib/levels.py`, model `default-2026.11.11`) still uses 10 layer rows of 2 conditions each (20 conditions), 3 states and a min per row. Treat that as legacy until https://github.com/bjcoombs/ai-native-toolkit/issues/744 lands. In that model a condition is one level of a row, so there are 20 conditions built from 26 criteria. Counts of 26 or 27 in older comments counted criteria or included proposed rows. Until https://github.com/bjcoombs/ai-native-toolkit/issues/747 lands, the report template in `skills/assess-findings/SKILL.md` still shows the legacy "X / 8" score, the band labels and the old Step names.

## Glossary

| Term | Meaning |
|---|---|
| **Condition** | One checkable practice, with file:line evidence. It is the unit of counting. |
| **Input class and grade** | Two layers, both in `lib/level_signals.py` (merged in https://github.com/bjcoombs/ai-native-toolkit/pull/802, part of https://github.com/bjcoombs/ai-native-toolkit/issues/783). Each signal has an **input class**: what its producer read. The seven classes are `config_text`, `head_text`, `head_structure`, `git_history`, `execution_artifact`, `ci_executed` and `platform`. The input class fixes a **grade**. `config_text` grades as `config_only`, `head_text` as `declared`, and `head_structure`, `git_history` and `execution_artifact` as `measured`. `ci_executed` and `platform` grade as `verified`. `measured` and `verified` count toward N; `config_only` and `declared` are listed, not counted. Earlier lists of five or four "signal classes" in #783 mixed the two layers. |
| **State** | Each condition has one state per run. **Met**: verified at this commit. **Unmet**: checked and absent, including a practice that was removed. **Not applicable**: nothing for it to act on, by a versioned rule. **Not measured**: the run could not see it (headless, no network, shallow history), shown with its reason. **Declined**: the repository declared an opt-out (https://github.com/bjcoombs/ai-native-toolkit/issues/828). |
| **Truth tag** | The truth a condition keeps honest. There are eight: `behaviour`, `contracts`, `docs`, `structure`, `agent_context`, `agent_runtime`, `governance`, `supply_chain`. Tags are mutual: no tag ranks above another, and they are not a ladder. Every condition has one or more tags (https://github.com/bjcoombs/ai-native-toolkit/issues/843). |
| **Layer (L0-L8)** | **Legacy.** Kept only as an alias, so old configs and fleet history resolve. New work groups by tag. |
| **Step** | A milestone for how much agent autonomy the repository can support. See Steps below. |
| **Gate** | A condition a Step requires, whatever the count. Gates are named after their condition, not after a tag or a layer. One exception to "declared is not counted": the `instructions` gate reads a declared signal (an instruction file is text at HEAD) and still holds Step 2. It is listed, not counted in N, but it satisfies the gate (#783). |
| **Hazard** | A harmful practice, often two good practices combined (for example, CI plus a label that skips it). A hazard blocks the Step whose gate it undermines. It never subtracts from N. |
| **Badge** | Do not use this word for a condition. In the report it means a state marker only (HAZARD, DECLINED, NOT MEASURED). |

## The headline

```
Step 2 · Supervised agent PRs · next: CI runs tests
Verified practices: 11 of 19 measured · 3 not measured: no GitHub access
agent_context 3 · agent_runtime 2 · behaviour 2 · structure 2 · docs 1 · governance 1 · contracts 0 · supply_chain 0
```

- **Line 1, the Step and the next unlock, leads.** A Step does not move when the rules add conditions, so this line is stable across model versions.
- **Line 2.** N is the number of met conditions. M is the number of **measured** conditions only. Not-measured conditions are shown beside the count with a reason, and are never in M, so a blind run neither lowers nor raises the score. When M grows because new checks land, the change is labelled "rules changed", not shown as a drop.
- **Line 3, the per-tag strip.** A condition with several tags appears in each tag's cell, but N counts it once.
- **There is no "Level N", no 0-8 score, no mean and no layer order.** Team roll-ups show the lowest Step plus "N of M measured" (https://github.com/bjcoombs/ai-native-toolkit/issues/772, https://github.com/bjcoombs/ai-native-toolkit/issues/806).
- The headline is computed as f(commit, model version) and never set by the LLM. Changes are attributed by cause: repo, rules or scanner (https://github.com/bjcoombs/ai-native-toolkit/issues/771, https://github.com/bjcoombs/ai-native-toolkit/issues/774).
- Implementation: https://github.com/bjcoombs/ai-native-toolkit/issues/844.

## Steps

| Step | Name | Gate added at this Step |
|---|---|---|
| 1 | Assisted: one supervised agent | none |
| 2 | Supervised agent PRs | `instructions`: an instruction file an agent reads |
| 3 | Unattended agent PRs | `ci_runs_tests`: CI runs the tests, unmasked (https://github.com/bjcoombs/ai-native-toolkit/issues/775) |
| 4 | Autonomous loops | `coverage_gate`: an enforced coverage gate |

- Gates accumulate, so Step 4 needs all three.
- A Step also needs a count threshold of met conditions. **The thresholds are not set yet.** They will be calibrated on the 12-repository corpus (https://github.com/bjcoombs/ai-native-toolkit/issues/842) and versioned with the model.
- An active hazard blocks the Step whose gate it undermines.
- A declined condition never satisfies a gate.
- Two data sets inform the model. A 36-repository private fleet spike (https://github.com/bjcoombs/ai-native-toolkit/issues/744) measured spread and gates. A 12-repository public calibration corpus (https://github.com/bjcoombs/ai-native-toolkit/issues/740 calibration comment, kept as the regression set in #842) found unscored practices and scanner defects.
- Gates are staged because the 36-repository spike found an enforced coverage gate in none of the 36. Requiring every gate for every Step would hold all of them at Step 1 (https://github.com/bjcoombs/ai-native-toolkit/issues/744).
- The Step names in this table replace the older names in `skills/assess-findings/SKILL.md` ("Parallel", "Supervised autonomy").

## What else is decided

- **Rules for new conditions.**
  - A condition is accepted only if, across the 12-repository corpus, at least one repository meets it and at least one does not (https://github.com/bjcoombs/ai-native-toolkit/issues/842).
  - A condition with an empty denominator reads not measured, never met (https://github.com/bjcoombs/ai-native-toolkit/issues/827).
- **Spread comes from interaction, guard and contradiction conditions, not from more file-presence checks.** The candidate conditions from the calibration are the children of https://github.com/bjcoombs/ai-native-toolkit/issues/826 (https://github.com/bjcoombs/ai-native-toolkit/issues/845 to https://github.com/bjcoombs/ai-native-toolkit/issues/858).
- **No general dependency graph.** Dependencies are conditions over two signals plus the three gates.
- **Outcome claims** (do agents do better on repositories that meet more conditions?) are a separate research track. They are reported as "observed" and are never added to N.

## Still open

- Step count thresholds, waiting on https://github.com/bjcoombs/ai-native-toolkit/issues/842.
- Wording: the headline says "Verified practices", but N counts both `measured` and `verified` grades (`_LISTED_GRADES` in `lib/level_signals.py` lists only `config_only` and `declared`). Either the label changes (for example "Evidenced practices") or `measured` is shown separately.

## Build order

1. **Done:** https://github.com/bjcoombs/ai-native-toolkit/issues/771 (re-score stored scans under the current model).
2. **Now:**
   - condition fixes https://github.com/bjcoombs/ai-native-toolkit/issues/775 and https://github.com/bjcoombs/ai-native-toolkit/issues/776 (children #781-#788);
   - the alias-map renames still open on https://github.com/bjcoombs/ai-native-toolkit/issues/783 (its signal classes already merged in https://github.com/bjcoombs/ai-native-toolkit/pull/802, so the engine does not wait on it);
   - the manifest skeleton https://github.com/bjcoombs/ai-native-toolkit/issues/792.
3. **Then the engine:** https://github.com/bjcoombs/ai-native-toolkit/issues/744.
4. **On the engine:**
   - https://github.com/bjcoombs/ai-native-toolkit/issues/827, https://github.com/bjcoombs/ai-native-toolkit/issues/828, https://github.com/bjcoombs/ai-native-toolkit/issues/842 and https://github.com/bjcoombs/ai-native-toolkit/issues/843;
   - then the headline, https://github.com/bjcoombs/ai-native-toolkit/issues/747 followed by https://github.com/bjcoombs/ai-native-toolkit/issues/844.
5. **New conditions:** the #826 children, after #792 and #843.

## Superseded

| Earlier position | Replaced by | Where |
|---|---|---|
| A 0-8 score across layers | The open-ended count of verified conditions | #740 huddle verdict |
| "Level N" as the headline, read/write/meta bands | The Step, then "N of M measured", then the tag strip | #740 decision of 2026-11-21 |
| Layers as the scoring frame | Truth tags; layers survive only as an alias | #740 decision of 2026-11-21 |
| Gates named L0, L5, L6, or by tag | Gates named after their conditions: `instructions`, `ci_runs_tests`, `coverage_gate` | this page |
| "Never lose what you've earned", "highest Step held" | Dropped; achievements are dated events only | #740 |
| Negatives lower the level | Hazards block Steps and never net | #828 |
| M includes conditions that were not measured | M is measured conditions only | this page |
