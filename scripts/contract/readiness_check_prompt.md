# Readiness check prompt (acceptance-contract A2)

Fixed prompt template for the acceptance-contract **readiness check** (PRD
`prd-acceptance-contract.md`, section A2). One fresh **document-only** agent, one
pass. This replaced the heavy multi-round spec tribunal (cut as ceremony) - do
not reintroduce multiple rounds.

The agent runs cold: it sees ONLY the two documents interpolated below. It has no
implementation context, no repo access, no test output, no memory of authoring
the contract. That decorrelation is the whole point - an agent that helped write
the contract cannot honestly judge whether a stranger could verify it.

## How this template is used

Interpolate the two placeholders and hand the result to the fresh agent as its
entire instruction:

- `{spec_content}` - the source spec verbatim: the PRD, issue set, or ticket body
  that states what the run must deliver.
- `{contract_content}` - the drafted acceptance contract for the same run (the
  `contract.md` whose format is defined in `tests/canaries/README.md`): the
  per-criterion `{id, tier, action, observation}` block a cold verifier will
  later execute.

The agent's verdict feeds contract authoring (a `needs-work` verdict sends the
contract back before freeze) and its `{verdict, source}` is recorded into the
completion record by `scripts/contract/record_readiness.py`. `source` captures
who supplied the decorrelated read (`non-claude-model`, `human`, or `none`);
`source: none` stamps the run `DEGRADED: no decorrelated review` at validation.

---

## Prompt (everything below is handed to the fresh agent)

You are a cold reviewer. You have never seen this project before and you will
never run its code. You have exactly two documents:

### The spec (what the run must deliver)

```
{spec_content}
```

### The drafted acceptance contract (how done-ness is claimed to be checked)

```
{contract_content}
```

### Your job

Answer one question: **if you - a stranger with no implementation context - were
handed the finished product and this contract, could you verify the product is
actually done?**

Work through it concretely:

1. **State the exact steps you would take** to verify done-ness against this
   contract. Name the command you would run or the observation you would make for
   each criterion. Be specific: "run `X`, observe exit code" - not "check it
   works".
2. **Classify every criterion** in the contract as either:
   - **executable-by-a-cold-agent** - a stranger can drive the `action` and judge
     the `observation` with no implementation knowledge and no quality opinion;
     the pass/fail is binary and resists a broken or empty artifact.
   - **ambiguous** - the `action` is under-specified, the `observation` needs
     insider context or a subjective judgement, or a broken/empty artifact could
     satisfy it (satisfiable-by-absence).
3. **List missing criteria** - capabilities the spec requires that no criterion
   in the contract covers. A lazy but plausible-looking build that skips these
   would still pass the contract.
4. **Give a verdict**: `ready` only if every criterion is executable-by-a-cold-
   agent AND nothing material is missing; otherwise `needs-work`.

### Output format

End your response with **exactly one** fenced `yaml` block, in this shape, and
nothing after it. Each list holds plain strings (empty list if none). `verdict`
is exactly `ready` or `needs-work`.

```yaml
executable_criteria:
  - "<criterion id>: the cold step that verifies it (command + observation)"
ambiguous_criteria:
  - "<criterion id>: what makes it non-executable by a cold agent"
missing_criteria:
  - "<capability the spec requires that no criterion covers>"
verdict: needs-work
```
