# Top 3 Actions: the case rules

Rules for particular kinds of Top 3 action, from "Filling the Top 3 Actions table" in `SKILL.md`.

**Currency-check any package you name (issue #62).** Before naming a specific package *version* in an action (`dart_code_metrics: ^5.7.6`, `some-linter@2.1.0`), prefer naming the **rule/concept plus a pointer to the language's package registry** (pub.dev, npm, crates.io, PyPI, RubyGems) over a hardcoded version - registries stay current; your training data doesn't. If you do name a specific package, verify it's still actively maintained for the repo's language/runtime version rather than trusting recall. This protects every language symmetrically: any canonical tool can be deprecated after the model's training cutoff (the `dart_code_metrics` case), so "name the rule + registry" is the safe default.

Good actions look like:

> _"Add `cyclop` rule (threshold 15) to `.golangci.yml`. Current per-function p95 is 23; immediate offenders by worst function: `internal/import/parser.go` (function ccn 67), `internal/sync/reconciler.go` (function ccn 54)."_

Cite the **per-function** value (`fn_ccn` / `max_fn_ccn`) against a per-function rule, not the file aggregate - a `cyclop:15` rule flags functions, so a file whose worst function is 12 is not an offender no matter how high its aggregate sums (issue #58).

Generic actions to avoid:

> ~~_"Improve code quality"_~~ - name the files and the threshold.
> ~~_"Add a linter"_~~ - name the linter, the rule, and the first three files it will flag.

**Read-side remediation must match the actual gap.** The diagnosis from agent instructions and runtime legibility changes the action:

- **Runtime legibility partly in place because observability is instrumented but not agent-reachable** (rung 1-2 - the `meridian` case): the action is _"make existing observability agent-queryable"_ - e.g. "add a `.mcp.json` log/metrics server" or "add a `view-logs` repo skill wrapping `logcli`" - **not** "add observability" (it's already there; the gap is reachability).
- **Agent instructions partly in place because a hub doc is stale:** name the specific stale hub from `stale_hubs` and its churning subject - _"refresh `docs/architecture.md` (251d stale; subject `src/api/` had 47 commits in window)"_ - not "improve the docs".
- **Agent instructions partly in place because the doc set is fragmented:** name the orphans / islands - _"link the 6 orphan docs into the MOC; `index.md` is named but not wired (out-degree 0)"_.
- **Coverage gates and truth pressure partly in place because coverage is enforced but truth-pressure is unverified:** the actions are _"strengthen assertions to pin observable behaviour at the named survivors"_ and _"add mutation testing to CI (e.g. `mutmut`, Stryker, PITest)"_ - **not** "raise the coverage threshold" (higher line-coverage numbers make coverage say more while the tests pin no more behaviour).

**Cross-check offenders against the recommendation's own scope (issue #59a).** When an action names a *scope* ("un-exclude `services/*/service/*.go`", "enforce the rule under `src/core/`"), every offender you list in that action's evidence must actually fall within that scope. Before writing the offender list, verify each path against the named pattern - a `service_modules.go` under `shared/pkg/saga/schema/` is **not** under `services/*/service/`, so it can't be cited as evidence for un-excluding `services/*/service/`. If the worst offenders sit outside the scope you're recommending, either widen the recommended scope to cover them or move them to a separate action with the right scope. The offender list and the recommended change must describe the same set of files.

**Apply the same truth-pressure to `/assess`'s own structural findings (issue #59c).** `/assess` grades other docs for being honest; its own topology/count claims are held to the same bar. Before stating any count-based finding - "the README lists 15 services but there are 19", "N modules lack a base doc" - **count the actual entries**, don't estimate or trust a single signal. For a service/component-count claim, enumerate both sides (what the doc lists vs what exists on disk) and report the real delta and the specific missing names, not a round-number guess. A wrong count ("missing 2" when it's actually missing 4, including specific named services) erodes trust exactly as a stale doc does. If you can't enumerate precisely, say "approximately" and name what you couldn't verify rather than asserting a false-precise number.

**Sub-threshold "approaching the cap" findings are not refactor tasks (issue #60).** Files in the *Watch* band but under the *High* threshold (e.g. 600-800 lines against an 800-line cap, or per-function ccn 10-14 against a 15 rule) are **not violations**. Do not default them into refactor tasks - pre-emptively decomposing files that currently work fine is churn-for-churn (review cost, merge conflicts, behaviour risk on working code). Instead:

- Keep actual violations (over the High threshold) as the standard remediation tasks.
- Surface "approaching the cap" files as an **optional, low-priority** item in *Additional Opportunities*, never the Top 3 - and frame it **annotate-first**: the recommendation is to add an acknowledgement marker (e.g. a `// large-file: tracked` comment or a lint allow-entry) so the file is consciously owned, leaving the decompose-or-not decision to the maintainer. Only escalate to a refactor task when the file is over the cap or the maintainer asks.
- Label the band explicitly in the report so a reader sees "under the cap, watch" - never present a sub-threshold file as if it failed a gate.
