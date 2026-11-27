# The completion report and retrospective

The layout of the report the marathon skill writes at Completion: the PR table, the PRD delivery table and the structured retrospective (Completion steps 4 to 7).

```
## Marathon Complete: <tag>

All <N> tasks done. <N> PRs merged.

<PR table: Wave | PR | Tasks | Title | Merged time>

### PRD Delivery

| Criterion | Status | Evidence |
|-----------|--------|----------|
| <success criterion from PRD> | Met / Partial / Not met | PR #<N>, ... |

<If any not met, explain what's remaining and whether follow-up tasks are needed.>

### Retrospective

**Template changes tested this run**
<Check marathon-retros.md Template Changes table for "Pending" items.
For each that was exercised: did it help, hurt, or not apply? Mark validated.>

**What worked well**
- <specific patterns, tools, or approaches — with evidence>

**What didn't work — with waste estimate**
- <friction point>: ~<N> min lost. Root cause: <X>
- <friction point>: ~<N> min lost. Root cause: <X>

**Lead decisions log**
- <decision>: <alternatives considered> -> <chosen> because <reason>
(task combinations, dependency changes, merge ordering, model upgrades, interventions)

**Where does this learning belong?** (specific command / cross-cutting rule / README / don't capture)
- <target file or section>: <concrete change with rationale>

**What clause is no longer earning its place?**
- <existing rule or block>: <reason it can be removed or demoted>

**Stats**
- Tasks: <N> completed, <N> cancelled/deferred
- PRs: <N> merged, <N> avg review iterations
- Teammates: <N> spawned (<N> sonnet, <N> opus), <N> needed intervention
- Wall clock: ~<N> min spawn to final merge
- Estimated waste: ~<N> min (CI waits, conflict resolution, stalled teammates)

**Flow** (from `flow_metrics.py`: per-PR table, per-run metrics against the previous run, a per-wave table of DAG width and units after combining, the run's top combining hot files, largest wait)
<paste the script's markdown output; name the cause of the largest wait>
```
