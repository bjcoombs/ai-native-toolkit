# Discovery mode: Loop-Until-Dry

The Loop-Until-Dry rules of the huddle skill, for topics classified discovery in Step 1.

For topics classified **discovery** in Step 1 only. Black Hat (and Green Hat where the topic is about generating options, not just risks) repeats until it stops surfacing anything new, instead of running once. A single risk-surfacing pass under-counts a long tail; one more round almost always finds another failure mode. Deliberation topics never enter this loop.

**Termination - whichever comes first:**
1. **Convergence**: two consecutive rounds add no new claims to the findings.
2. **Hard cap**: 5 rounds per hat.

```
round = 1
prev_empty = false
while round <= 5:
    run_hat_phase(hat)              # discovery semantics: "what have we NOT yet named?"
    new_claims = claims this round absent from the accumulated findings
    if new_claims == [] and prev_empty:   # two consecutive empty rounds
        break                              # converged
    prev_empty = (new_claims == [])
    round += 1
if round > 5:
    record "hard cap" in the verdict
```

**What counts as a new claim** (reuse the Hat Findings `claim` field):
- Restating a prior claim in different words - **not new**.
- Elaborating a prior claim with additional evidence - **new** (adds information).
- Introducing a new risk/opportunity - **new**.

**Verdict disclosure.** If the hard cap stops the loop before convergence, the verdict must say so - no silent truncation: e.g. "Discovery loop: Black Hat ran 5 rounds (hard cap), did not fully converge - additional risks may exist." On convergence, record the round count plainly.

This loop applies in every mode - solo (Blue Hat re-runs the hat agent), phased (re-spawn the hat sub-agent per round with the running synopsis), and team (re-announce the hat phase to members each round). In all modes the chair runs the new-claim test against the accumulated findings.

**Non-discovery topics retain single-pass behaviour, unchanged.** This mode adds behaviour only to discovery-classified topics; every deliberation huddle runs exactly as it did before, save for the new Coverage line from the completeness-critic pass.
