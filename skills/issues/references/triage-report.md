# The triage report

The layout of the report the Triage Report step of the issues skill renders before it stops.

```
## Issue Triage: <org>/<repo>

Tagged agent-ready: #12, #15, #18
Tagged needs-triage:
- #20, #21: research posted (Needs your call: 1 each)
- #22: overlaps open PR #40 (both edit skills/issues/SKILL.md); after it merges: <what remains>
Promoted on confirmation: #19 (reading A folded into the issue body)

Sizing:
- #12: one PR - single module, reviewable in one sitting
- #15: one PR - splitting the doc and test changes would only add ceremony
- #30: decompose (pending approval) - migration must land before the two consumers
  #30 parent: <deliverable acceptance criteria> (verification unit)
  ├─ child A: schema migration          (one PR)
  ├─ child B: consumer X, blocked by A  (one PR)
  └─ child C: consumer Y, blocked by A  (one PR)

Dependencies authored:
- #18 blocked by #15 - #15 adds the config key #18 reads

Execution order:
  Wave 1: #12, #15, child A
  Wave 2: #18, child B, child C
  After children merge: #30 parent verification

Overlaps:
- #22 <-> PR #40: skills/issues/SKILL.md (held as needs-triage)
- #12 <-> #15: README.md (additive, left parallel)

Approve the decomposition of #30 and the execution order? Reply to proceed (this creates the
sub-issues), or re-run /issues to begin on the agent-ready issues.
```

With no decomposition proposed, the report ends instead with:
```
OK to start on the agent-ready issues in this execution order? Re-run /issues to begin, or reply to proceed.
```
