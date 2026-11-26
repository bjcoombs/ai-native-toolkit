# lib/fleet

The library behind `/fleet`: it runs the `/assess` scans over many repositories and turns
the results into roll-ups, a run history, diagrams, a report and a published page. The
command line is `skills/fleet/scripts/fleet.py`; this package holds everything it calls.

## Where to start

- `runner.py` scans each checkout in its own process and writes one counts-only row per
  repository to `repos.jsonl`. Everything else reads those rows.
- `fleet_groups.py` rolls the rows up to one row per group, in `groups.jsonl`.
- `fleet_data.py` builds `fleet-data.json`, the bundle the fleet page reads.

## The rest, by job

| Job | Modules |
|---|---|
| Run setup and output | `fleet_cli_args`, `fleet_stamp`, `fleet_method`, `fleet_hashed`, `fleet_cleanup`, `fleet_coverage`, `fleet_includes`, `fleet_include_list`, `fleet_platform`, `fleet_labels` |
| Published schemas | `fleet_schema`, `fleet_group_schema`, `fleet_level_schema`, `fleet_data_schema`, `fleet_achievements_schema` |
| Levels, Steps and tags | `fleet_level`, `fleet_maturity`, `fleet_data_steps`, `fleet_data_tags`, `fleet_data_units`, `fleet_units`, `fleet_repo_facts`, `fleet_step_*`, `fleet_why` |
| History and comparison | `fleet_history`, `fleet_compare`, `fleet_scanner_diff`, `fleet_rescore`, `fleet_trends`, `fleet_legacy`, `condition_effect`, `condition_demotion` |
| Targets and achievements | `fleet_targets`, `fleet_targets_import`, `fleet_achievements`, `fleet_achievements_runs`, `fleet_credit` |
| Report, pages and diagrams | `fleet_view`, `fleet_next_steps`, `fleet_opportunities`, `fleet_opportunities_section`, `fleet_learn_from`, `fleet_front`, `fleet_diagrams`, `fleet_svg`, `fleet_heatmap`, `fleet_slope`, `fleet_radar`, `fleet_tooling_map`, `fleet_tag_heatmap`, `fleet_tag_profile`, `fleet_agent_building`, `fleet_doc_freshness` |
| The fleet page | `fleet_page`, `fleet_page_data`, `fleet_page_assets`, `fleet_page_live`, `fleet_publish`, `fleet_publish_session` |

Each module's docstring says what it does. The co-change rules this package shares with the
check manifest and the maturity model are in [lib/README.md](../README.md).

## Before you change it

- The repository rows are counts-only: they hold numbers and fixed codes, never text copied
  from a scanned repository. The rules for each field live in the check manifest.
- Every writer names `newline="\n"`, so Windows and Linux write the same bytes.
- A change to a collector's output needs a new `revision` for that collector; the fleet
  golden test fails otherwise.
