# tests

The repository-level test suite: the plugin contract, the bash skills, the acceptance
gates and the CI wiring. Run it with `make test-contract` from the repository root. It is
the required `plugin contract pytest` check. No test here calls a model or the network.

The `/assess` engine has its own suite in `skills/assess/tests/`, and the `scripts/`
package has one in [scripts/tests/](../scripts/tests/README.md).

## Where to start

`test_plugin_contract.py` is the core. It turns the invariants in
[CLAUDE.md](../CLAUDE.md) into assertions: skill and agent frontmatter, `TRIGGER`
clauses, relative links that resolve, and the agent catalog. When a pull request breaks a
rule in `CLAUDE.md`, this file is usually the one that fails.

## What else lives here

| Files | What they pin |
|---|---|
| `contract/` | The acceptance-contract gates in `scripts/contract/` and the canary harness in `scripts/canaries/` |
| `canaries/` | The fixtures the canary harness runs; [canaries/README.md](canaries/README.md) defines the contract file format |
| `test_ghsync_*.py`, `test_ghreport*.py` | The `/ghsync` and `/ghreport` bash scripts, run offline against stubbed `gh` and `glab` |
| `ghsync_gitlab_env.py`, `ghreport_gitlab_env.py`, `gitbash.py`, `test_gitbash.py` | Shared fixtures and helpers that run the bash scripts the same way on every OS, and the test for those helpers |
| `test_file_size_ratchet.py` | Python files under the ratchet roots may shrink but not grow |
| `test_make_regen.py` | `make regen` rewrites every generated file and nothing else |
| `test_merge_queue_workflows.py`, `test_windows_workflow.py` | The required checks report on merge-queue groups and on the split Windows run |
| `pytest_shard.py`, `test_pytest_shard.py` | The shard plugin that splits the Windows run, and its test |
| `test_windows_docs.py`, `test_windows_smoke_check_svg.py`, `test_windows_smoke_longpath.py` | The Windows setup guide, the SVG check in the Windows smoke step, and the long-path fixture and scanner check |
| `test_issue_forms.py` | The issue forms, the machine-readable block in `docs/contributing/definition-of-ready.md` and the `needs-triage` workflow's trigger and permissions |
| `test_landing_page.py` | The skill index on the GitHub Pages landing page |
| `test_marathon_flow_metrics.py` | The marathon flow metrics script |
| `test_refine.py`, `test_refine_platform.py`, `refine_env.py` | The backlog refinement script in `skills/refine/scripts/`, replaying recorded `gh` output from `fixtures/refine/` |
| `test_refine_workflows.py`, `test_refine_write.py`, `test_refine_apply.py`, `refine_write_env.py` | The refiner workflows' triggers, permissions, tool limits, call caps and `workflow_call` inputs, and the write scripts' guardrails in `skills/refine/scripts/`, replaying recorded `gh` output from `fixtures/refine_write/` |
| `test_skill_forge_instruction_files.py` | `/skill-forge` on instruction files such as `CLAUDE.md` and `AGENTS.md` |
| `test_refine_project.py` | The sweep's project sync (`skills/refine/scripts/refine_project.py`): the Status table, open questions, no-op reruns, dry-run, the write cap and the project-shape check, replaying recorded `gh project` output |
| `test_refine_adoption_docs.py` | The `uses:` and `include:` examples in `README.md` and `docs/refine.md` against the real workflow and template paths, inputs, secrets and release pin |
| `fixtures/` | Inputs for the Windows smoke checks and the recorded `gh` output for the refinement script |
