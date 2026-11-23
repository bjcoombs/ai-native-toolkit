## Output: `report.md` and diagrams

Every run draws diagrams from its own rows, with no model call, and writes them beside
`groups.jsonl` with `DIR/report.md` embedding each one as a relative image link (it renders
in GitHub and GitLab Markdown previews). A diagram that cannot be drawn is skipped and the
report says why in one line; stderr names it too. A diagram failure never changes the exit
code, and the run history still keeps only its three files. `--no-diagrams` turns this off.
The report opens with the [coverage line](../SKILL.md#coverage---discovered), then "What changed and who
earned it" (see [`achievements.json`](achievements.md#output-achievementsjson)), then "Opportunities",
before the diagrams. It closes with "How this was measured": a link to the catalog of every
check (`docs/what-we-measure.md` in the toolkit repository), the scans that ran (with how
many repositories could not measure each, and the reason codes) and the scans that were
skipped, each with its reason (only a full `/assess` run builds it, it needs the network or
`--platform`, or `--scans` left it out).

**Opportunities.** Rendered from `fleet-data.json`'s [`opportunities`](fleet-data.md#opportunities), so
the two always agree. It opens with what is already in place (per row, how many measured
repositories hold its top level), then the biggest lift with its change and "done when",
each shared template's lift, and **Recent progress**: the units that reached a new level
since the previous comparable run, read from this run's `level_up` achievements. Recent
progress is display only: it comes from the run history and never feeds a level or
`fleet-data.json`. On the first kept run it says the run is the starting line; under
`--no-history` or `--full` it says the run is not kept in the history. Without `--levels`
the section is left out. Logic: `lib/fleet/fleet_opportunities_section.py`.
Every `## ` section opens with a line that starts `In place:` and says what this run already
has (what is measured, reached or earned) before the section's question and any next step;
the team next-steps pages open every heading the same way. The wording rules, the words
user-facing output leaves out and the guard test that enforces both are in the
module docstring of `skills/assess/scripts/lib/vocabulary.py`.

**Maturity, one number.** The quadrant, the trend lines and the slope chart all plot the
same mean: each `team` row's level as a share of the row's top level, averaged over the rows
with a known level (unknown rows are left out, never counted as 0). `org` rows are left out of
all three: an org row reads the group's settings, not a repository, so the per-repository
quadrant cannot measure it, and the group charts use the same rows so the three read on one
scale. The per-layer bars (`layer-bars.svg`) were retired in #1073: the fleet rolls up by truth
tag, and `fleet-data.json` carries the per-tag tally each tag drawing reads.

| File | Question | Built from |
|------|----------|------------|
| `quadrant.svg` | Which busy repositories have the most room to grow? | Each repository's commits in the window (`ai_usage.total_commits`, non-merge) across, and up its mean maturity: every `team` row of the `--levels` maturity model evaluated on that repository alone, as a share of the row's top level, averaged over the rows that could be measured. The busy (at or above the median) and below-half quadrant is shaded. Skipped without `--levels` or without team rows. |
| `shared-tooling.svg` | What does a shared template change reach, and which pins differ from the fleet's? | `shared_tooling`: repositories linked to the CI sources and marketplaces they reference, with each item's dependent count. Drift: a CI include pinned to a ref other than the fleet's most common ref for that source (ties go to the lexically greatest ref) is dashed, and the SVG caption states this rule; an unpinned include is counted as unpinned, not drift; a pin counts-only could not keep, or a ref that is a CI variable (`$TPL_REF`), is dotted as pin unknown, never unpinned or drift. |
| `trend.svg` | Is each team improving? | The run history: each group's mean maturity (each known team row's level as a share of its `top_level`, the slope chart's unit) in every comparable dated run, oldest left, this run right. Comparable: same window, scans, output mode, path hashing and `--fleet-config` hash, with a maturity model; one run per date, the newest 12. A change of maturity-model version breaks every line, marked by a dashed rule. A history entry written before `groups.jsonl` carried `top_level` takes the top levels from this run's maturity model when it used the same version; otherwise (an older maturity-model version) it is not drawn, so the axis is always 0 to 1 share of top level, and the caption names it ("N earlier runs on an older maturity-model version not shown"). End labels give the last value and the change over the last unbroken stretch. Skipped with fewer than 3 comparable share runs (the skip line names any runs left out), without `--levels` or without team rows, or when the run is not kept in the history (`--no-history`, `--full`). |
| `slope.svg` | What moved since the earlier run, and which row moved it? | The `--compare` result: each group's mean level (a team row's level as a share of its top level) in the earlier run and this one, over the team rows with a known level in both runs, labelled with the row that moved most; a `*` marks a move where the group's measured set of repositories moved too, or the scanner changed (`scanner_changed`, `toolkit_changed_unknown_scope`). Skipped when no earlier run was compared (no `--compare`, or a bare `--compare` matched no history entry), when the runs are not comparable (the reasons are named), without `--levels` or without team rows, or when no group has a team row with a known level in both runs. |

Unknown is never drawn as zero: a repository with no measured row or no commit count, or no
`shared_tooling` evidence, a group with no row known in both compared runs, or a repository
with an undetermined level on a row, is named on a hatched strip or segment, and a group
with no level in a run breaks its trend line and is named on a hatched note. An unknown row with
a confirmed `floor` is shown as "at least N" beside each known-only mean a floor-inclusive figure labelled as a lower bound (tooltips and
the not-plotted strip), never moving a plotted point. Colours follow the
viewer's light or dark scheme. The SVGs have no script and no external font or link, and the
same run draws the same bytes. Logic: `lib/fleet/fleet_diagrams.py`, `lib/fleet/fleet_quadrant.py`,
`lib/fleet/fleet_tooling_map.py`, `lib/fleet/fleet_trends.py`, `lib/fleet/fleet_slope.py`,
on the shared helpers in `lib/fleet/fleet_svg.py`.
