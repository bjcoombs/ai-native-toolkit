# Flow control: sizing the team by merge throughput

Brooks' law applies to an agent team. Each builder adds communication paths and integration work, and the serial part of the run (the re-check, the merge, the lead's own attention) caps the speed-up. The rules below keep the team no larger than the merge step can absorb. Each rule carries its reason and the run 4 evidence (2026-11-22 to 23, issue #1089) that produced it.

The [Flow metrics](flow-metrics.md) reference measures whether these rules worked: merges of main per PR, the wait from first green to merged, and throughput per hour.

## 1. WIP limit by merge throughput

**Rule.** Cap open builder PRs at about the number the merge step clears in one CI cycle, plus one in review. To get the basis, divide the CI cycle (push to required-green, in minutes) by the lead's per-merge time (re-check plus merge plus cleanup), then add one. Spawn a new unit only when the open count is below the cap. Report the limit and its basis at each wave: `WIP limit: 4 (CI cycle 20 min / 5 min per merge, plus 1)`. At the next wave, recompute it from the measured `mean_wait_green_to_merged_min` and throughput.

**Reason.** A builder whose PR cannot merge does not add throughput. It goes stale, merges main again, and restarts CI and review for everyone. The CPU count and the number of ready units are the wrong limit, because neither is the constraint.

**Evidence.** Seven builders produced about six merges an hour, the rate the serial re-check allowed (#1088). #1039 merged main five times before it landed.

## 2. Design gate before public-surface and cross-cutting units

**Rule.** Before spawning a unit that changes a public surface (a headline, a badge, a schema, the CLI) or a cross-cutting rule (contract criteria, the manifest format), record a design decision first: a short huddle or a `triage:decision` comment on the issue. The teammate builds to that record. A unit with no record stays out of the wave.

**Contract criteria over a growing manifest.** A criterion over a manifest or registry the run is still adding to must not pin an entry count. It states the property instead: every entry has field X, or every new module has a manifest entry. A pinned count fails on every merge that adds an entry, and each failure costs a re-freeze.

**Reason.** A public surface is read by people outside the run, and a cross-cutting rule is read by every other unit. Reversing either after merge costs more than the design check before it.

**Evidence.** The frozen contract was re-frozen 9 times. Its criteria pinned entry counts for a manifest the run was still growing. A public headline shipped at 01:35 (#844, PR #1028) and a huddle reversed it at 07:40 (#1083).

## 3. Area ownership

**Rule.** Group units by area (for example the CI reader, the fleet, the manifest, a module move) and assign each area to one standing teammate where the queue allows. The area owner takes that area's units in sequence on fresh branches. Record the owner per area in the tracking file (`meta.areas`), and route each area's units, review fixes and conflict questions to its owner.

This is the one exception to "one task, one teammate" in [Ephemeral Teammates](../SKILL.md#ephemeral-teammates): an area owner keeps its context across units in its area, and still takes one unit per PR.

**Reason.** Conflicts between two units in one area then resolve inside one head, not between two PRs and the lead. A standing owner also learns the area once. A cold teammate per unit re-learns the repo each time.

**Evidence.** Cold teammates re-learned the repo on every spawn, which is where run 4's old-path imports after the module moves came from, along with the fixes for stale lead messages.

## 4. Batch small same-file units

**Rule.** Combine small units that edit the same non-generated file even when that file is not hot, within the existing ~complexity 8 ceiling and the other limits in [Step 1](../SKILL.md#step-1-dag--hot-file-analysis). This extends hot-file combining; it does not replace the ceiling or the rule against collapsing a wave.

**Reason.** Two small PRs on one file cost two CI cycles, two reviews and one merge of main for whichever lands second. One combined PR costs one of each.

**Evidence.** #1061 with #1046, and #898 with #899: each pair is two small edits to one file.

## 5. Lead failure modes

The lead is the serial part of the run. These rules stop its own mistakes from stopping the merge step.

| Rule | Reason | Run 4 evidence |
|------|--------|----------------|
| **Never block a deadline step on a question a recorded decision answers.** Act on the recorded decision, flag the question in the status update, and ask in parallel. | A blocking question stops every merge behind it, and nobody is there to answer it during an unattended run. | A question asked of the maintainer held the 3.0.0 release for over two hours and missed its 05:30Z target, while the answer was already recorded. |
| **Read the time with `date -u`.** Never take it from message stamps or from memory. | Message stamps are in mixed zones and can be stale. Deadlines and the waits in the flow metrics need one clock. | The lead mis-timed the 3.0.0 release, and merges stopped for hours (#1088). |
| **Send a "merged" notice only after the merge call returns MERGED.** Confirm with `gh pr view <n> --json state` before telling a teammate or a peer session that a PR landed. | A teammate that merges main on a false notice builds on a tree without the change, and the correction reaches every PR. | Listed among the run 4 lead's own failure modes (#1089). A wrong broadcast reaches every open PR before a correction can: AR-84 went to every PR and needed AR-88. |
| **Lift every hold the moment a freeze ends.** Keep held PRs in a list in the tracking file (`meta.holds`), and clear the whole list when the freeze ends. | A hold with no list outlives the freeze silently, and a green PR then waits for nothing. | Two PRs sat green for 2.5 hours behind a release freeze that was never lifted for them. |
| **Re-check every PR in the same feature chain as a just-merged PR, even when no hot file changed.** A chain is a set of units that build on one another's behaviour: the same issue family, the same module move, the same contract criterion. | The hot-file list catches textual conflicts, not semantic ones. Two PRs that each pass CI can still break main together. | Two merges that each passed CI broke main for 25 minutes together (#1038 and #1044). The re-check caught #1039 against #1023 when each PR's own CI passed. |

## 6. Moved modules: port edits by hand

**Rule.** After a module moves behind a shim (the old path re-exports or aliases the new one), an edit made at the old path does not merge across. Port it by hand to the new path, then take the shim from main unchanged. Put this rule in every spawn prompt while a move is in flight or has just merged, and name the moved paths.

**Reason.** Git records the move as a modify of the old file plus an add of the new one. A parallel edit to the old file then conflicts with the shim, or lands on the shim and never reaches the code that runs.

**Evidence.** The lead's first advice was that git would carry an old-path edit across the fleet move (AR-84). That was wrong with a shim at the old path; AR-88 corrected it after a modify/modify conflict on #1066 against #1063's manifest move. Every later move notice (AR-89, AR-93, AR-95) repeated the rule.
