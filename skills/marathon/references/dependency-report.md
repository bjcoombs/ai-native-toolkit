# The dependency analysis report

The layout of the plan that Step 1 of the marathon skill reports before Wave 1 (Step 1, item 8).

   ```
   ## Dependency Analysis: <tag>

   Critical path: <task-ids> (<N> points sequential)
   Parallel capacity: <M> tasks in first wave

   WIP limit: <N> open builder PRs (<CI cycle> min / <per-merge> min per merge, plus 1)
   Design gate: <units needing a recorded decision, and where each is recorded or pending>
   Area owners: <area>: <teammate> (<unit ids>)

   Combined tasks:
   - Tasks <X>+<Y>: <reason> (single teammate, single PR)

   Hot files:
   - <file-path>: tasks <ids> (pattern: <e.g., "accept both sides">)

   Optimizations:
   - Removed dependency <X> → <Y>: different modules

   Staleness outcomes:
   - Unit <X> <-> open PR <M>: <combine | sequence after the PR | kicked back> (<shared files>)

   Verification units:
   - <parent>: children <ids> (<merged>/<total> merged; checked after <last child>, or skipped: <reason>)
   ```
