## Output: `fleet-data.json`

One JSON document per run beside `groups.jsonl`: the versioned bundle the fleet
artifact reads (#692). Keys sorted, compact, every list sorted; the same inputs give the
same bytes. Schema: [`references/fleet-data.schema.json`](fleet-data.schema.json).
Logic: `lib/fleet/fleet_units.py`, `lib/fleet/fleet_repo_facts.py`, `lib/fleet/fleet_data.py`. It is kept in
the run history beside the other files.

| Key | Value |
|-----|-------|
| `fleet_data_version` | Document version (4: version 4 added `step`, `next_step`, `tally`, `exclusions` and `gate_blockers` on repositories and `step` on units; version 3 added `opportunities`; the achievements engine reads versions 1 to 4 alike). |
| `run` | The run stamp's fields from `run.json`, copied one by one: every field but `fleet_stamp_version` and `fleet_config_entries` (`platform` reads `false` for a stamp written before it). A new stamp field fails a test until it is copied or excluded here, with a `fleet_data_version` bump. |
| `model` | Each maturity model row: `id`, `title`, `scope`, `top_level`. |
| `views` | One entry per view, `ownership` and `system`, each with its `units`. |
| `repos` | One entry per repository row, sorted by `key`. |
| `opportunities` | The [opportunities](#opportunities) this run's data shows: what is in place, the biggest lift and the shared-template leverage. `null` without `--levels`. |

**Units.** A unit is an org, division, department, team or system. Each has a `key`
(`<view>/<id>`), `view`, `parent` (the parent's key, `null` at a root), `kind`, `source`
(`path`, `github_teams`, `codeowners` or `mapping`), `name`, `owners` and `owner_count`,
`repos` (repositories below it at any depth), and its roll-ups. The ownership view is
built from, in order:

1. the repository path: GitLab nested groups (`grp/sub/proj` sits in `grp/sub`, under `grp`);
2. GitHub teams from a `--settings` fragment's `teams` list (a repository in several teams takes the deepest, then the first slug);
3. CODEOWNERS, as a fallback for a repository in a one-segment group no team placed: a
   catch-all (`*`) owner that is a team under its group (`@acme/platform`, matched
   ignoring case, so `@Acme/Platform` too) places it in that team;
4. the `--units` mapping, which always wins.

An inferred unit's kind follows its place: a root is `org`, a unit that holds a
repository is `team`, any other is `division` one level down and `department` deeper.
An inferred unit the mapping left with no repository is dropped. The system view comes
from the mapping alone. A repository sits in at most one unit per view, so it is counted
once per view.

The mapping file:

```toml
[[unit]]
view = "ownership"        # or "system"; default "ownership"
id = "acme/platform"      # unique within its view
kind = "division"         # org, division, department, team, system
parent = "acme"           # optional, a unit of the same view
name = "Platform"         # optional
owners = ["@acme/leads"]  # optional

[[repo]]
repo = "acme/svc1"        # a repos.jsonl id
ownership = "acme/platform"
system = "checkout"
owners = ["@acme/payments"]  # optional; replaces the CODEOWNERS owners
```

A mapped repository not in the run is skipped and named on stderr.

**Roll-ups.** Per unit, one entry in `levels` per `team` row: `measured` of `repos`
("8 of 12 measured": an unknown or failed repository is counted in `repos`, never read
as 0) and `lowest_level`, the lowest level a measured repository holds on the row
(`null` when none was measured). Each unit also has a `step` roll-up (#944): `measured` of
`repos` (repositories with a Step; a failed one counts in `repos` only, never as Step 1),
`lowest_step` (the lowest Step a measured repository holds, `null` when none is), and
`next_step` for the Step above it (`null` at the top Step or when none is measured): `step`,
`held` (measured repositories below it) and `gates`, every gate of that Step in model order
with `held` (repositories it holds back) and, of those, `not_measured` and `excluded` (the
owner excluded the gate's condition, so the Step stays unreached). Each unit also has a `tags`
roll-up (#1073): `measured` of `repos` (repositories with a per-tag tally; a failed one counts in
`repos` only, never as zeros) and `cells`, one per truth tag any measured repository carries, in
the catalog's tag order, each count summed over the measured repositories. The roll-up holds no average and names no
repository (#772: the no-comparison rule of #740), so `fleet-data.json` version 2 has no
`mean_share`, `weakest` or `overall`. `org` rows read a group's settings, not a
repository, so they have no unit roll-up; their levels are in `groups.jsonl`.

**Repositories.** Each has `key` (its id), `status`, `reason`, `active`, `web_url` (the
`origin` remote as `https://<host>/<path>`, credentials, an ssh port and `.git` dropped; `null`
for a local remote), `owners` and `owner_count` (the mapping's owners, else the
CODEOWNERS catch-all owners), `units` (`{view, unit}` per view it sits in), and `rows`,
one cell per `team` row: `status`, `level`, `top_level` and the criterion behind it. Below the top level, `signal` and `value` are the first unmet criterion of the
next level and its measured value; at the top level both are `null`; an unknown cell
names the signal that could not be read and `reason` (`unavailable`, `type_mismatch`, or
`not_scanned` for a failed repository). `step`, `next_step` and `tally` are the
repository's [`level.json`](level.md) entry for the same commit (`null` when failed or
without `--levels`), and `exclusions` lists the conditions the owner excluded in its
`.assess/config.toml` (#910): `id`, `kind`, `source` and `reason`, listed and never
counted. The reason is the owner's own words, so it is written only under `--full`
(`null` otherwise); `id` and `kind` are always written. `gate_blockers` (#945) lists every
gate of the model's `[[steps]]` that does not hold, once per gate id in Step order, as
tokens only: `gate` (the gate id), `state` (the blocking condition's state) and
`disposition` (`excluded_by_owner`, else `null`); empty when every gate holds or the
repository is not measured. A Step target above the repository's next Step reads
its missing gates from it. `tags` (#1073) is the per-tag strip of the repository's own
headline for the same level result: one cell per truth tag a condition carries, in the
catalog's tag order, with `tag`, `met`, `measured` and `verified` (counted like the tally)
and `not_measured`, `hazards` and `declined` (the NOT MEASURED, HAZARD and DECLINED counts).
A condition with several tags is in each of their cells and counted once in the tally.
`tags` is `null` when the repository is not measured (failed, or without `--levels`). A
`fleet-data.json` written before this field has no `tags`: a reader takes that, or a
malformed tally, as not measured, never as zero (`lib/fleet/fleet_data_tags.py`
`member_tags`), and a re-score under a new maturity-model version rebuilds it from
the stored scans. Tag names come from the fixed truth-tag list and every other field is a
count, so the tally names no repository, path or owner.

**Tag profile.** `tag_profile` (#940, `lib/fleet/fleet_tag_profile.py`) is one row per truth
tag, every tag in the catalog's order, read from those per-repository tallies (never a second
tally): `conditions` and `countable` (the model's conditions with the tag, and those the tally
can count by grade), `repos` (repositories with a per-tag tally), `measured` (those with a
measured condition on the tag), and `median`, `p25` and `p75`, the measured repositories'
share met (`met` over `measured`) with linear interpolation, 4 places; all three are `null`
when no repository is measured on the tag, never 0. `tag_profile` is `null` without
`--levels`; a document written before it has none. The fleet's `tag-profile.svg` draws it.

### Unit rows

A run with `--monorepo-units` (#866) scans each repository by monorepo unit, and each
repository carries `unit_rows`, one per unit, so the page can expand a repository into its
units. Each row has:

- `key`: the unit's folder. Written in clear only under `--full`; with `--hash-paths` or
  `--hash-salt` it is the folder's salted hash, which joins the same unit across runs under
  one `--hash-salt`; `null` in plain counts-only output.
- `kind`: the unit's kind, a token kept in counts-only output: `skill` for a folder holding
  a `SKILL.md`, `agents` and `commands` for a plugin's components, `package` for a
  build-manifest unit (#1098). A skills marketplace repository's rows are its skills, so an
  estate of marketplace repositories reads skill by skill. `null` in rows rebuilt from
  scans stored before units had a kind.
- `step`: the unit's Step, from the unit's own readings over the repository's scans, the
  same result `/assess` gives the unit in `run-context.json` `unit_levels`. A unit's own
  readings keep the fields the default maturity model reads; a `--levels` criterion on
  another field of a scope-sensitive block (such as `coverage_gate.configured`) finds no
  value for the unit.
- `next_step`: the Step above; `null` at the top Step.
- `next_blocker`: the first gate of that Step that does not hold, as tokens: `gate`,
  `state` and `disposition`, as `gate_blockers` writes them; `null` when no gate blocks it.
- `tags`: the unit's per-tag tally, the shape of the repository's `tags`.

A unit row carries no condition tally (`tally`, "N of M measured"): compare units inside
one repository by Step, next blocker and per-tag counts. `unit_rows` is `null` when the
repository was not scanned with the unit scans (its scans carry no available
`unit_readings` block: no `--monorepo-units` and no `--scans unit_readings`, a failed scan,
or an unavailable block) or the run has no `--levels`; a re-score of stored rows that carry
the block rebuilds them. A measured repository with no unit has an empty list.

**Privacy.** `--full` keeps everything. Counts-only (the default) keeps keys, which carry
the group and team paths (`ownership/acme/platform`) and repository ids unless
`--hash-paths` or `--hash-salt` is given, and tokens and
web URLs (so the host name of each remote appears, in `--out` and the run history), writes a string `value` that is not a short token as `set`, and drops unit
names and owners (`owner_count` stays). With `--hash-paths` or `--hash-salt`, every unit
and repository key, and every reference to one, is hashed (`h:` plus 16 hex digits) and no
name, owner or web URL is written: the file names no repository, group or host. Hashes
join across runs only under one `--hash-salt`. The rules are `FLEET_DATA_RULES` in
`lib/counts_only_rules.py`. Its repository keys are the hashed ids every other output
file writes ([Hashed runs](repos.md#hashed-runs)), so a `fleet-data.json` repository joins its
`repos.jsonl` row; `achievements.json`, built from it, carries the same keys. GitHub team slugs from a `ghreport` fragment's `teams` list
appear only as unit keys, so they are hashed with them.

### Opportunities

`opportunities` (#746) turns the run into the changes that would lift the most
repositories. It is computed from this run's rows and the maturity model alone, with no
model call and no history, so the same run gives the same bytes. Every line is phrased as
an opportunity, nothing ranks or numbers a team or repository (names are listed in key
order), and no insight names a person.

| Key | Value |
|-----|-------|
| `measured` | Repositories scanned `ok`. |
| `in_place` | Per `team` row: `row`, `at_top` (measured repositories already at its top level) and `measured`. The report opens the section with it. |
| `biggest_lift` | The single next-level criterion whose completion would lift the most repositories: `row`, `level` (the level they would reach), `signal`, `repo_count`, `repos` (sorted keys), `gate` and `step` (the Step gate this condition is, else `null`), `change` and `done_when` (the same wording as the team pages), and `text`. A repository counts only when its row level is known and that criterion is the one unmet criterion of its next level; an unknown never counts. Ties go to maturity-model row order, then level. `null` when no single change lifts a measured repository. |
| `shared_templates` | One entry per shared CI source (a `shared_tooling.ci_source_ids` id at least 2 repositories include) and row level it would unlock: `source` (the id, such as `gitlab_project:grp/ci-templates`), `kind`, `includers`, `row`, `level`, `signal`, `repo_count`, `repos` and `text`. Its `repos` are the including repositories whose one unmet next-level criterion reads a `ci_checks.` signal: what the template would lift if it ran tests or another enforced check. Sorted by `source`, then row order. |

Privacy follows the keys: `repos` and `source` are in clear in counts-only output (shared
tooling ids stay readable) and hashed under `--hash-paths` or `--hash-salt`. A hashed
`source` is the scan's own hash, so it matches `ci_source_ids` in `repos.jsonl`; `text` holds
counts and maturity-model titles only. Recent progress, the units that reached a new level
since the previous run, is not in this file: it comes from the run history, is display
only and appears only in `report.md`. Logic: `lib/fleet/fleet_opportunities.py`.
