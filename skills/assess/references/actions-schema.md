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

## Per-action fields

```json
{
  "rank": 1,
  "action": "Investigate the src/foo.go <-> src/bar.go seam",
  "done_when": "The coupling is documented in a contract, or the shared state is extracted",
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
| `done_when` | string | The exit criterion. Without it a weak executor doesn't know when to stop. |
| `scope_fence` | string | What NOT to touch. Without it a weak executor over-extends. |
| `status` | enum | Lifecycle: `pending` \| `claimed` \| `done` \| `reopened`. See below. |
| `claimed_by` | string \| null | Identifier of the executor that claimed the action; `null` while unclaimed. |
| `completed_sha` | string \| null | The commit SHA that satisfied `done_when`; `null` until done. |
| `mode` | enum | Deterministic execution posture, derived from `finding`: `characterize_first` \| `verify_then_retire` \| `refactor_safe`. See below. |

### Recommended (passed through when the LLM supplies them)

`layer`, `effort`, `files`, `first_step`, and `finding` are carried through verbatim if present. `finding` is the finding type the action addresses; it drives `mode` derivation and, together with `files`, forms the action's identity for status carry-forward, so supplying both is what keeps a completed action's lifecycle attached to it.

## `status` lifecycle

| Status | Meaning |
|--------|---------|
| `pending` | Open, unclaimed. The initial state of every newly written action. |
| `claimed` | An executor has taken the action but not finished it. |
| `done` | Completed; `completed_sha` records the commit that satisfied `done_when`. |
| `reopened` | A later run re-flagged work a prior run had marked done. |

**Carry-forward across runs.** Each `/assess` run recomputes `rank`, `mode`, `done_when`, and `scope_fence` from the freshest findings, but preserves `status`, `claimed_by`, and `completed_sha` for any action that matches an entry in the existing `actions.json`. A done action therefore stays done, with its completed SHA and claimant intact, when the assessment is re-run. An action that no longer appears in the new Top 3 simply drops out of the contract.

**How an action is matched.** Two keys, tried in order:

1. **Identity** - the action's `finding` plus the paths it names (its `files` list, or a singular `path`). Paths are compared as a set, so reordering or repeating an entry changes nothing.
2. **Directive text** - the `action` string, byte-for-byte.

The directive is written afresh by the model on every run, so identity is tried first: a reworded action that still names the same finding and the same files keeps its lifecycle. Text is the fallback because an action with no `finding`, or none naming a path, has no deterministic identity - and because a contract written before identity matching existed carries no `finding` at all, and must still carry its statuses forward on the first run after the upgrade. Each prior entry is indexed under both keys, so both cases resolve. A key holds every entry filed under it, in rank order, because text keys genuinely collide: two prior actions on different files routinely carry the same canned directive, and keeping only one of them would hide the other.

The same finding on a different file is a different piece of work: it does not inherit the other entry's status. The text fallback is what would otherwise let it, because the directive is **not** free text per file - the core renders one canned phrase per finding type (`FINDING_ACTIONS` in `lib/keyhole_signals.py`) with the path in its own column, so two hotspots sharing a finding carry byte-identical directives. A text match is therefore refused when the two entries share no file. The test is disjointness rather than inequality, because `files` is transcribed by the model: an action that named two files and now names three is still that action, and refusing it would reset completed work. One file in common is enough to call it the same work. The refusal compares the files, not the identities, because `finding` is recommended rather than required: either entry may name files while carrying no finding, and both directions have to hold. An entry naming no file at all is not evidence of different work - it is the shape a pre-change entry and a judgement slot both have - so a text match stands when either side has nothing to compare.

Where several prior entries share a directive, the one sharing a file wins outright; an entry with nothing to compare is taken only when no candidate shares a file, and the earliest is taken, so the result follows rank order rather than file order.

**Worked examples.** A prior contract holding `investigate the seam` / `hidden_coupling` / `["src/a.py"]`, done at `abc123`, and `investigate the seam` / `hidden_coupling` / `["src/b.py"]`, done at `def456`:

| New action | Matches | Why |
|-----------|---------|-----|
| `pin the a.py contract` / `hidden_coupling` / `["src/a.py"]` | `abc123` | Identity hit. The directive was reworded; the finding and the file are unchanged. |
| `investigate the seam` / `hidden_coupling` / `["src/a.py", "src/c.py"]` | `abc123` | Identity misses because the file set grew. Text hits, and the candidates overlap on `src/a.py`. |
| `investigate the seam` / `hidden_coupling` / `["src/z.py"]` | nothing, starts `pending` | Text hits both candidates, but the files are disjoint from both. A shared canned phrase is not evidence of shared work. |
| `investigate the seam`, no `finding`, no `files` | `abc123` | Nothing to compare, so the text stands, and the earlier entry by rank is taken. |

## `mode` derivation

`mode` is derived deterministically by the finalize step from the action's `finding` type (via `FINDING_MODES` in `lib/keyhole_signals.py`) - it is never guessed by the LLM. Each mode traces to one of the write-side tendencies the toolkit guards against:

| Mode | Posture | Finding types |
|------|---------|---------------|
| `characterize_first` | Understand/contract the code before changing it - the risk is acting blind on an unpinned seam or complexity. | `hidden_coupling`, `unexplained_complexity`, `untrusted_hotspot`, `orphaned_understanding`, `override_contradicts_signals` |
| `verify_then_retire` | A self-description that may be lying - verify whether it is still true, then delete / ticket / escalate. Never trust it as-is. | `lying_map`, `self_referential_tests`, `unactioned_intent`, `candidate_dead_weight` |
| `refactor_safe` | A bounded island safe to restructure in isolation. | `refactor_boundary`, `accretion_ratchet` |

An action whose `finding` is absent or unrecognised defaults to `characterize_first` - the conservative "understand before you touch it" posture.

## Versioning

- **v1** (`schema: 1`): `{schema, actions:[{rank, action, done_when, scope_fence, ...}]}`. No lifecycle fields, no `mode`, no top-level `run_id`.
- **v2** (`schema: 2`): adds per-action `status` / `claimed_by` / `completed_sha` / `mode` and top-level `run_id`.

The finalize step reads a v1 `actions.json` for carry-forward without error: a v1 entry contributes no lifecycle fields, so a re-run over a v1 contract initialises every action to `pending`.
