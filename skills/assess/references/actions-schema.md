# `actions.json` schema

`.assess/actions.json` is the durable, machine-readable Top 3 Actions contract. `assess_finalize.py` writes it from the LLM-authored `actions` array in `finalize-input.json`. Unlike the report's markdown table, it is meant to be parsed: an executor agent - often a smaller, cheaper model than the one that ran the assessment - reads it to know what to do, how to verify it, where to stop, and whether the work is still open.

## Top-level shape

```json
{
  "schema": 2,
  "run_id": "20260707T101500Z-ab12cd",
  "actions": [ /* one entry per action, sorted by rank */ ]
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | int | Schema version. Currently `2`. |
| `run_id` | string \| null | The run that produced this contract, copied from `run-context.json`. `null` when the run carried no id (legacy). Ties the actions back to the assessment that raised them. |
| `actions` | array | The Top 3 Actions, one object per row, sorted ascending by `rank`. |
| `completed` | array, optional | Prior `done` entries of a reviewable finding (`self_referential_tests`) that no action in this run's Top 3 matched, kept as they were. An action that later matches one comes back as `reopened`, not `done`. Written only when non-empty. A done review that cleared its finding (below) leaves the Top 3, and this list is what keeps the record. |

**A done review clears `self_referential_tests` (#721).** That finding keys on commit origin, so no code change clears it; its action asks for a review instead. When the `actions.json` committed at HEAD holds an entry (in `actions` or `completed`) with `finding: "self_referential_tests"`, `status: "done"`, a `completed_sha` that is an ancestor of HEAD, and the flagged **source** path in `files`, and no commit since `completed_sha` touched that path, the path leaves the attention list and `prescribed_actions`. It stays in run-context `derived_findings` with a `reviewed` note citing the sha, and the findings section lists it under "Reviewed". A later change to the source re-flags it. An entry that names only the test file does not clear the source path; commit `actions.json` for the review to count. See the `scripts/lib/report/reviewed_findings.py` module docstring.

## Per-action fields

```json
{
  "rank": 1,
  "action": "Investigate the src/foo.go <-> src/bar.go seam",
  "done_when": "`docs/contracts/foo-bar.md` documents the src/foo.go <-> src/bar.go coupling, or the shared state is extracted",
  "scope_fence": "Only src/foo.go and src/bar.go; do not touch callers",
  "status": "pending",
  "claimed_by": null,
  "completed_sha": null,
  "mode": "characterize_first",
  "finding": "hidden_coupling"
}
```

### Required (written every time)

| Field | Type | Meaning |
|-------|------|---------|
| `rank` | int | Priority order (1 = do first). |
| `action` | string | The directive. Also the **fallback identity** used to carry status across re-runs, for an action with no `finding` or no path. |
| `done_when` | string | The exit criterion. Without it a weak executor doesn't know when to stop. It holds a backticked command or path, a number or a Markdown link (the top card's line rule, `lib/card_lint.py`); `assess_finalize.py` warns, naming the action, when it holds none. |
| `scope_fence` | string | What NOT to touch. Without it a weak executor over-extends. |
| `status` | enum | Lifecycle: `pending` \| `claimed` \| `done` \| `reopened`. See below. |
| `claimed_by` | string \| null | Identifier of the executor that claimed the action; `null` while unclaimed. |
| `cost` | object, optional | A removal's measured cost, `{metric, path, value}` (`metric` `always_on_lines`: the instruction lines every session loads), copied from the `gap_actions` proposal. Carried through verbatim. |
| `completed_sha` | string \| null | The commit SHA that satisfied `done_when`; `null` until done. The next run credits the action under "Closed since last run" when this commit landed after the commit the previous run measured (`lib/since_last_run.py`, #1083); commit `.assess/actions.json` for it to count; an uncommitted file reads "not known", never 0. |
| `mode` | enum | Deterministic execution posture, derived from `finding`: `characterize_first` \| `verify_then_retire` \| `refactor_safe` \| `remove`. See below. |

### Recommended (passed through when the LLM supplies them)

`layer`, `condition`, `effort`, `files`, `first_step`, `finding`, `cost` and `sub_items` are carried through verbatim if present. `condition` is the **condition id** of the maturity-model condition the action moves (`<row id>.<level>`, e.g. `layer-5-ci.1`), as the run's level result lists it in `run-context.json` `level.conditions[].id`; it ties the advice to the same conditions as the headline's Step and verified tally (#747). `assess_finalize.py` checks it before writing anything: an id the level result does not list, or a condition named on a run with no level result, is refused with a non-zero exit naming the id, and `actions.json` is left unchanged. An action without `condition` (a finding-driven action) is accepted as before. `finding` is the finding type the action addresses; it drives `mode` derivation and, together with `files`, forms the action's identity for status carry-forward, so supplying both is what keeps a completed action's lifecycle attached to it.

### `sub_items` (optional, #1263)

The report's lettered sub-actions (`2a. <change> - Done when: <criterion>`, written under the Top 3 table) for the entry. Each is an object:

| Field | Type | Meaning |
|-------|------|---------|
| `label` | string | The letter, `a`, `b`, ... The rendered reference is the parent's `rank` plus the label (`2a`); a leading number in the label is replaced by the parent's rank, so `2a` under rank 3 renders `3a`. |
| `action` | string | The sub-action's directive. |
| `done_when` | string | Its own exit criterion, in the same form as the entry's. |
| `first_step` | string, optional | Its own first step. The parent's first step is not inherited: it belongs to the parent's change. |
| `scope_fence` | string, optional | Its own fence; the parent's applies when absent. |

`assess_actions_render.py items` files each sub-item as its own tracker item after its parent, titled `2a. <action>`, with the parent's finding line, a line naming the parent action, and the parent's Context block. The PR table keeps one row per entry. A sub-item missing `label`, `action` or `done_when` is skipped with a warning; the entry still renders. Lifecycle fields (`status`, `claimed_by`, `completed_sha`) belong to the entry, not to a sub-item. An entry without `sub_items` reads as before, so the schema stays `2`.

## `status` lifecycle

| Status | Meaning |
|--------|---------|
| `pending` | Open, unclaimed. The initial state of every newly written action. |
| `claimed` | An executor has taken the action but not finished it. |
| `done` | Completed; `completed_sha` records the commit that satisfied `done_when`. |
| `reopened` | A later run re-flagged work a prior run had marked done. |

**Carry-forward across runs.** Each `/assess` run recomputes `rank`, `mode`, `done_when`, and `scope_fence` from the freshest findings, but preserves `status`, `claimed_by`, and `completed_sha` for any action that matches an entry in the existing `actions.json`. A done action therefore stays done, with its completed SHA and claimant intact, when the assessment is re-run. An action that no longer appears in the new Top 3 drops out of `actions`; if it was a `done` `self_referential_tests` action, it moves to `completed`, and an action that matches a `completed` entry again is `reopened`.

**How an action is matched.** Two keys, tried in order:

1. **Identity** - the action's `finding` plus the paths it names (its `files` list, or a singular `path`). Paths are compared as a set, so reordering or repeating an entry changes nothing.
2. **Directive text** - the `action` string, byte-for-byte.

The directive is written afresh by the model on every run, so identity is tried first: a reworded action that still names the same finding and the same files keeps its lifecycle. Text is the fallback because an action with no `finding`, or none naming a path, has no deterministic identity - and because a contract written before identity matching existed carries no `finding` at all, and must still carry its statuses forward on the first run after the upgrade. Each prior entry is indexed under both keys, so both cases resolve. A key holds every entry filed under it, in rank order, because text keys genuinely collide: two prior actions on different files routinely carry the same canned directive, and keeping only one of them would hide the other.

The same finding on a different file is a different piece of work: it does not inherit the other entry's status. The text fallback is what would otherwise let it, because the directive is **not** free text per file - the core renders one canned phrase per finding type (`FINDING_ACTIONS` in `lib/keyhole/keyhole_signals.py`) with the path in its own column, so two hotspots sharing a finding carry byte-identical directives. A text match is therefore refused when the two entries share no file. The test is disjointness rather than inequality, because `files` is transcribed by the model: an action that named two files and now names three is still that action, and refusing it would reset completed work. One file in common is enough to call it the same work. The refusal compares the files, not the identities, because `finding` is recommended rather than required: either entry may name files while carrying no finding, and both directions have to hold. An entry naming no file at all is not evidence of different work - it is the shape a pre-change entry and a judgement slot both have - so a text match stands when either side has nothing to compare.

Where several prior entries share a directive, the one sharing a file wins outright; an entry with nothing to compare is taken only when no candidate shares a file, and the earliest is taken, so the result follows rank order rather than file order.

**Worked examples.** A prior contract holding `investigate the seam` / `hidden_coupling` / `["src/a.py"]`, done at `abc123`, and `investigate the seam` / `hidden_coupling` / `["src/b.py"]`, done at `def456`:

| New action | Matches | Why |
|-----------|---------|-----|
| `pin the a.py contract` / `hidden_coupling` / `["src/a.py"]` | `abc123` | Identity hit. The directive was reworded; the finding and the file are unchanged. |
| `investigate the seam` / `hidden_coupling` / `["src/a.py", "src/c.py"]` | `abc123` | Identity misses because the file set grew. Text hits, and the candidates overlap on `src/a.py`. |
| `investigate the seam` / `hidden_coupling` / `["src/z.py"]` | nothing, starts `pending` | Text hits both candidates, but the files are disjoint from both. A shared canned phrase is not evidence of shared work. |
| `investigate the seam`, no `finding`, no `files` | `abc123` | Nothing to compare, so the text stands, and the earlier entry by rank is taken. |

## `mode` derivation

`mode` is derived deterministically by the finalize step from the action's `finding` type (`lib.report.gap_actions.action_mode`: `REMOVAL_MODES` for a removal proposal, else `FINDING_MODES` in `lib/keyhole/keyhole_signals.py`) - it is never guessed by the LLM. Each mode traces to one of the write-side tendencies the toolkit guards against:

| Mode | Posture | Finding types |
|------|---------|---------------|
| `characterize_first` | Understand/contract the code before changing it - the risk is acting blind on an unpinned seam or complexity. | `hidden_coupling`, `unexplained_complexity`, `untrusted_hotspot`, `orphaned_understanding`, `override_contradicts_signals` |
| `verify_then_retire` | A self-description that may be lying - verify whether it is still true, then delete / ticket / escalate. Never trust it as-is. | `lying_map`, `self_referential_tests`, `unactioned_intent`, `candidate_dead_weight` |
| `refactor_safe` | A bounded island safe to restructure in isolation. | `refactor_boundary`, `accretion_ratchet` |
| `remove` | Take a practice away because it costs more than it pays, keeping every gate in place (#1084). Advice and credit only: the number of practices is never scored. Once done, the pruning ledger credits it in "since last run" ("Pruned: ..., saved N always-on lines", the measured drop) after two runs on new commits with the file unflagged and no gate lost. | `instruction_always_on` (a removal proposal from `gap_actions`, not a keyhole finding) |

An action whose `finding` is absent or unrecognised defaults to `characterize_first` - the conservative "understand before you touch it" posture.

## Versioning

- **v1** (`schema: 1`): `{schema, actions:[{rank, action, done_when, scope_fence, ...}]}`. No lifecycle fields, no `mode`, no top-level `run_id`.
- **v2** (`schema: 2`): adds per-action `status` / `claimed_by` / `completed_sha` / `mode` and top-level `run_id`.

The finalize step reads a v1 `actions.json` for carry-forward without error: a v1 entry contributes no lifecycle fields, so a re-run over a v1 contract initialises every action to `pending`.
