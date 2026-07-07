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
| `action` | string | The directive. Also the **stable identity** used to carry status across re-runs (rank reshuffles between runs; the directive text does not). |
| `done_when` | string | The exit criterion. Without it a weak executor doesn't know when to stop. |
| `scope_fence` | string | What NOT to touch. Without it a weak executor over-extends. |
| `status` | enum | Lifecycle: `pending` \| `claimed` \| `done` \| `reopened`. See below. |
| `claimed_by` | string \| null | Identifier of the executor that claimed the action; `null` while unclaimed. |
| `completed_sha` | string \| null | The commit SHA that satisfied `done_when`; `null` until done. |
| `mode` | enum | Deterministic execution posture, derived from `finding`: `characterize_first` \| `verify_then_retire` \| `refactor_safe`. See below. |

### Recommended (passed through when the LLM supplies them)

`layer`, `effort`, `files`, `first_step`, and `finding` are carried through verbatim if present. `finding` is the finding type the action addresses; it drives `mode` derivation and is worth supplying for that reason.

## `status` lifecycle

| Status | Meaning |
|--------|---------|
| `pending` | Open, unclaimed. The initial state of every newly written action. |
| `claimed` | An executor has taken the action but not finished it. |
| `done` | Completed; `completed_sha` records the commit that satisfied `done_when`. |
| `reopened` | A later run re-flagged work a prior run had marked done. |

**Carry-forward across runs.** Each `/assess` run recomputes `rank`, `mode`, `done_when`, and `scope_fence` from the freshest findings, but preserves `status`, `claimed_by`, and `completed_sha` for any action whose `action` directive matches an entry in the existing `actions.json`. A done action therefore stays done, with its completed SHA and claimant intact, when the assessment is re-run. An action that no longer appears in the new Top 3 simply drops out of the contract.

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
