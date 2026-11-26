## Output: `report.md` and diagrams

Every run draws diagrams from its own rows, with no model call, and writes them beside
`groups.jsonl` with `DIR/report.md` embedding each one as a relative image link (it renders
in GitHub and GitLab Markdown previews). A diagram that cannot be drawn is skipped and the
report says why in one line; stderr names it too. A diagram failure never changes the exit
code, and the run history still keeps only its three files. `--no-diagrams` turns this off.
The report opens with the front screen, "Fleet at a glance", then the
[coverage line](../SKILL.md#coverage---discovered), then "What changed and who
earned it" (see [`achievements.json`](achievements.md#output-achievementsjson)), then "What's
working across the fleet", then "Opportunities", before the diagrams.

**Fleet at a glance** (#1255, `lib/fleet/fleet_front.py`) answers three questions on one
screen: how many repositories sit at each Step (the Step funnel's counts, with repositories
that have no measured Step counted apart), how many moved up and how many moved down a Step
since the compared run (the Step movement grid's counts; moves that rule changes alone explain
are counted apart, and a run with nothing compared says it is the baseline), and how many are
one change from their next Step. It then ranks the gates by the repositories each would take to
their next Step (`opportunities.step_lifts` summed by gate), most first. Gates are ranked;
repositories never are, and none is named. Team pages (`teams/<group>.md`, written by
`fleet_next_steps.py`) are linked in alphabetical order under `--full` and `--hash-paths`
(by hashed id); a plain counts-only run links only the `teams.md` index, so the section names
no group or repository.

"What changed and who earned it" counts this run's achievements by kind, one line per kind,
and counts the units with a next level-up; each line links `achievements.json`, which lists
them, and the fleet page lists each unit's credit. It closes with "How this was measured": a link to the catalog of every
check (`docs/what-we-measure.md` in the toolkit repository), the scans that ran (with how
many repositories could not measure each, and the reason codes) and the scans that were
skipped, each with its reason (only a full `/assess` run builds it, it needs the network or
`--platform`, or `--scans` left it out).

**What's working across the fleet** (#1334) names the practices every measured repository holds
at the top level, then up to three more held most widely, each with how many measured
repositories hold its top level, from `opportunities.in_place`.

**Opportunities.** Rendered from `fleet-data.json`'s [`opportunities`](fleet-data.md#opportunities), so
the two always agree. Its `In place:` line points at "What's working across the fleet", which
states the counts once; then the biggest lift with its change and "done when",
each shared template's lift, and **Recent progress**: the units that reached a new level
since the previous comparable run, read from this run's `level_up` achievements. Recent
progress is display only: it comes from the run history and never feeds a level or
`fleet-data.json`. On the first kept run it says the run is the starting line; under
`--no-history` or `--full` it says the run is not kept in the history. **Learn from**
(#1155) closes the section: per repository, and per monorepo unit, whose next gate another
one already holds, the change and up to three that hold it (`learn_from` in
`fleet-data.json`), at most 20 lines. Plain counts-only output names no one there, so the
block says it is not shown; under `--hash-paths` the names are hashed ids. Without `--levels`
the section is left out. Logic: `lib/fleet/fleet_opportunities_section.py`.
Every `## ` section opens with a line that starts `In place:` and says what this run already
has (what is measured, reached or earned) before the section's question and any next step;
the team next-steps pages open every heading the same way. The wording rules, the words
user-facing output leaves out and the guard test that enforces both are in the
module docstring of `skills/assess/scripts/lib/vocabulary.py`.

**Maturity, one number.** The trend lines and the slope chart plot the
same mean: each `team` row's level as a share of the row's top level, averaged over the rows
with a known level (unknown rows are left out, never counted as 0). `org` rows are left out of
both: an org row reads the group's settings, not a single repository. The activity and maturity
quadrant (`quadrant.svg`) was dropped in #1255, because it placed repositories against each other
(#806); a run removes any copy an earlier run left. The per-layer bars (`layer-bars.svg`) were retired in #1073: the fleet rolls up by truth
tag, and `fleet-data.json` carries the per-tag tally each tag drawing reads.

| File | Question | Built from |
|------|----------|------------|
| `tag-profile.svg` | Which truth tags hold across the estate, and how far apart are the repositories on each? | `fleet-data.json` `tag_profile` (#940), read from each repository's per-tag tally (#1073): per truth tag, in the fixed tag order, a dot at the median share of the tag's measured conditions met across the repositories measured on it and a whisker from the 25th to the 75th percentile (with one repository it collapses to the dot). The row label carries the tag's condition count and the repositories measured on it. A tag no repository measured is a dashed row with its reason, never 0. Shown first, as the summary view. Skipped without `--levels`, when the run wrote no `tag_profile` (no `fleet-data.json` roll-up), or when no repository has a per-tag tally (none was scanned). |
| `tag-heatmap.svg` | Which tags have the most room to grow across the estate, and on which repositories? | `fleet-data.json` `repos[].tags` and `repos[].step` (#940): one row per repository, every repository of the run, sorted by Step (highest first, no Step last) and then key, never grouped by unit and with no unit or group name; one column per truth tag in the fixed order. Each cell is the repository's share of the tag's measured conditions met, shaded on the growth ramp and printed as a percentage; a cell with no measured condition is hatched, never shaded and never 0, and a tag no condition carries yet says "none yet" in its header. Rows carry the repository id only with `--full`, the hashed id with `--hash-salt`, and otherwise `repository N`. The drill-down under the median strip. Skipped without `--levels`, without `fleet-data.json`, or when no repository has a per-tag tally. |
| `step-funnel.svg` | Where does each repository stand, and which gate holds it there? | `fleet-data.json` `repos[].step` and `repos[].next_step` (#941) with the model's `[[steps]]`: one bar per Step with the count of repositories at it. Each repository counts once, under the first gate of its next Step that does not hold, in model order, so a bar's parts sum to its count; the label beside the bar also counts the repositories more than one gate holds back. A gate not measured is a hatched part with its reason code; one the owner set aside is hatched too. A repository with no measured Step is counted on a hatched line by its reason, never at Step 1. No repository is named. Skipped without `--levels`, without Steps in the model or without `fleet-data.json`. |
| `one-change-away.svg` | Which single change would take the most repositories to their next Step? | `fleet-data.json` `opportunities.step_lifts` (#941): one bar per condition, ranked by the repositories that meeting it alone would take to their next Step, labelled with the gate, the row title and the Step reached. A lift counts toward the Step the change reaches, past the next Step when the gates above it already hold (the level result's `ceiling_step`, #1333); such a bar is marked a multi-Step jump and its entry reads `multi_step: true`. A repository counts only when exactly one condition of its next Step's gates is not met and it is unmet (never not measured or set aside). Repositories waiting on a gate not measured, and those with no measured Step, are counted on hatched lines, never as a lift. No repository is named. Skipped as the funnel is. |
| `radar.svg` | Where is each unit strong across the practices, and where is it not yet measured? | `fleet-data.json` `views[].units[].levels[]` (#1367, counts from #1366): one radar per unit with a repository, ownership units then system units in key order, as small multiples; never ranked and never overlaid. Units nest, and every unit is drawn at every depth, as the fleet page and the achievements treat them: a unit's counts cover every repository below it, so a parent's radar redraws its children's repositories; a nested unit's subtitle and table column name the unit it sits in, and the caption says units nest. One axis per `team` practice with a known level in at least 20% of the run's repositories (`--radar-min-measured`, compared in whole percents, the fleet page's spoke rule; the practices left out are named in the caption). Each point is the unit's `met` over `measured`: the share of its measured repositories at the practice's top level, with rings at 25, 50, 75 and 100% and a tooltip naming the unit, the practice and met/measured. No mean of levels is taken. A practice the unit measured on no repository has a hatched mark at the rim, never a point at 0, and the outline breaks there. `report.md` prints the same counts under the picture as a table, one line per practice and one column per unit. The section's opening line counts the run's repositories at the top level on at least one drawn practice, each once, never adding units together. Units carry their name under `--full`, their hashed key under `--hash-salt`, and otherwise `<view> unit N`. Skipped without `--levels`, without team practices, without `fleet-data.json`, with no unit holding a repository, or when no practice reaches the threshold. |
| `shared-tooling.svg` | What does a shared template change reach, and which pins differ from the fleet's? | `shared_tooling`: repositories linked to the CI sources and marketplaces they reference, with each item's dependent count. Drift: a CI include pinned to a ref other than the fleet's most common ref for that source (ties go to the lexically greatest ref) is dashed, and the SVG caption states this rule; an unpinned include is counted as unpinned, not drift; a pin counts-only could not keep, or a ref that is a CI variable (`$TPL_REF`), is dotted as pin unknown, never unpinned or drift. |
| `trend.svg` | Is each team improving? | The run history: each group's mean maturity (each known practice's level as a share of its `top_level`, the slope chart's unit) in every comparable dated run, oldest left, this run right. Comparable: same window, scans, output mode, path hashing and `--fleet-config` hash, with a maturity model; one run per date, the newest 12. A change of maturity-model version breaks every line, marked by a dashed rule. A history entry written before `groups.jsonl` carried `top_level` takes the top levels from this run's maturity model when it used the same version; otherwise (an older maturity-model version) it is not drawn, so the axis is always 0 to 1 share of top level, and the caption names it ("N earlier runs on an older maturity-model version not shown"). End labels give the last value and the change over the last unbroken stretch. Skipped with fewer than 3 comparable share runs (the skip line names any runs left out), without `--levels` or without team practices, or when the run is not kept in the history (`--no-history`, `--full`). |
| `slope.svg` | What moved since the earlier run, and which practice moved it? | The `--compare` result: each group's mean level (a practice's level as a share of its top level) in the earlier run and this one, over the practices with a known level in both runs, labelled with the practice that moved most; a `*` marks a move where the group's measured set of repositories moved too, or the scanner changed (`scanner_changed`, `toolkit_changed_unknown_scope`). Skipped when no earlier run was compared (no `--compare`, or a bare `--compare` matched no history entry), when the runs are not comparable (the reasons are named), without `--levels` or without team practices, or when no group has a practice with a known level in both runs. |
| `step-movement.svg` | How many repositories changed Step since the earlier run, and how many moved only because the rules changed? | The `--compare` run and this run's `fleet-data.json` (#941): a grid of counts with the Step in the earlier run as rows and the Step now as columns, plus a not-measured row and column, a row for repositories new in this run and a column for those not in it (never shown as a drop). The earlier Step is its stored `level.json` (kept in the run history); its scans are also scored again under this run's model, and a move that score already explains is counted apart in its cell, dashed with a hatched mark. A maturity-model version change alone is bridged that way; any other reason the runs are not comparable replaces the grid with the reasons. Skipped when no earlier run was compared, without `--levels`, without Steps or without `fleet-data.json`. |

Unknown is never drawn as zero: a repository with no measured row or no commit count, or no
`shared_tooling` evidence, a group with no row known in both compared runs, or a repository
with an undetermined level on a row, is named on a hatched strip or segment, and a group
with no level in a run breaks its trend line and is named on a hatched note. An unknown row with
a confirmed `floor` is shown as "at least N": beside each known-only mean, a floor-inclusive figure labelled as a lower bound (tooltips and
the not-plotted strip), never moving a plotted point. Colours follow the
viewer's light or dark scheme. The SVGs have no script and no external font or link, and the
same run draws the same bytes. Logic: `lib/fleet/fleet_diagrams.py`,
`lib/fleet/fleet_tooling_map.py`, `lib/fleet/fleet_trends.py`, `lib/fleet/fleet_slope.py`,
on the shared helpers in `lib/fleet/fleet_svg.py`.

**Embedding and tooltips.** Each part with a hover `<title>` (a heat map cell, a radar point,
a trend point, a slope line, a shared-tooling link, a one-change-away bar, a tag row or cell)
carries the same values as `data-*` attributes, plain and with a value not measured left out,
never 0 (#1360). An SVG embedded with `<img>` shows no tooltip at all, on hover or on touch.
To show the details in a page, embed the SVG inline or with `<object>` and include the
dependency-free tooltip helper `skills/assess/templates/diagram-tooltip.js` (relative to the
repository root): it reads the data attributes, falls back to the `<title>`, and shows on
hover or tap. Each diagram is scoped, so several sit on one page without restyling each other.
The embed rules, the helper's behaviour and every diagram's attributes are in
`skills/assess/references/embedding-diagrams.md`. The fleet page (`fleet-view.html`) still
embeds its pictures as images; showing their tooltips there is #1313.

## The fleet page: `fleet-view.html`

With `--levels`, the run also writes `fleet-view.html` beside `report.md` (#946,
`lib/fleet/fleet_page.py`). It is one file that works offline. Its CSS and JavaScript are
inline, the run's data is embedded as JSON, and the tag and Step pictures are embedded as
images. It makes no network request and works out no level, Step or achievement: every number
comes from `fleet-data.json` and `achievements.json`. Two runs over the same inputs write the
same bytes. `--no-diagrams` removes it.

The page opens on the ownership root, or on the unit a viewer pinned in this browser. Its
sections, in order:

1. **Fleet at a glance**: the front screen above, for the whole fleet, from the same counts
   (`front` in the embedded data). Its team links are the ownership view's team units, by
   name, each opening that unit on the page.
2. **Since last run**: what this run credited to the unit and its repositories, and the
   Recount when the rules changed.
3. **Where this unit stands**: the lowest Step over its measured repositories, "N of M
   measured", the gates the next Step waits on, and the practices met by truth tag.
4. **Your target**: the unit's Step target, the gates not yet held and how many repositories
   each holds back. The static page has no target entry.
5. **Drill-down**: the units below (by name, never by rank), a table of the unit's own
   repositories with a cell per maturity-model row, and a repository's detail. Hovering a cell
   names its state, the next condition and what the next Step needs. A not-measured cell is
   hatched with its reason, never 0.
6. **Practice shape**: one radar chart for each unit in the shown unit's subtree, by name,
   never overlaid. A spoke is a maturity-model practice measured in at least 20% of the
   fleet's repositories; its point is the unit's repositories at the top level out of those
   measured (met/measured, a count and never a mean), with rings at 25, 50, 75 and 100%.
   Hovering a point names the unit, the practice and met/measured. A practice with nothing
   measured in the unit breaks the outline and has no point, never 0. A table under the charts
   gives the same counts, practice by unit. Above 8 spokes each spoke is labelled by its number,
   which the table's practice column repeats; hovering the spoke names the practice.
7. **Fleet pictures**: the drawn tag and Step SVGs.

Names follow the run's output mode. `--full` names repositories and units by their keys and
paths and shows an exclusion's reason. A hashed run shows hashed ids. Plain counts-only shows
`repository N` and `<kind> N` (such as `group N` or `team N`). No page shows a person or an
owner, and only a `--full` page shows a path. The page's data layer has one interface
(`load`, `subscribeTargets`, and `setTarget` only on a live source), so a published page can
read a database instead of the embedded data.
