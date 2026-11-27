# skills/assess/scripts

The command-line entry points of the `/assess` engine. Each script here is thin: it reads
its arguments, calls into [lib/](lib/README.md) and writes its output. The logic lives in
`lib/`, which never imports a script from this folder.

## Where to start

`assess_core.py` is the orchestrator. It runs every scan and writes `run-context.json`,
the file the rest of `/assess` reads. Read it first, then [lib/README.md](lib/README.md)
for how the scans plug in.

## The scripts

| Script | What it does |
|---|---|
| `assess_core.py` | Runs the scans for one repository and writes `run-context.json` and the `.assess/` wiki |
| `assess_signals.py` | Runs the same scans headless and writes one JSON document outside the repository; `/fleet` uses it |
| `assess_finalize.py` | Fills in the parts of the wiki the model writes after the core has run |
| `assess_report.py` | Renders the report from `run-context.json` with no model |
| `assess_gate.py` | The CI gate: fails a pull request that drops below the configured floors |
| `assess_emit_workflow.py` | Writes the CI workflow that runs the gate in a target repository |
| `assess_actions_render.py` | Renders the end-of-run offers from `actions.json` |
| `assess_fix.py` | Writes starter files for the next blocking gate (`/assess --fix`) |
| `assess_tour.py` | The guided first run on a sample repository (`/assess --tour`) |
| `complexity-treemap.py` | Draws the complexity hotspot treemap |
| `doc-graph-svg.py` | Draws the doc link graph |
| `gen_catalog.py` | Writes the measures catalog from the check manifest (`make catalog`) |
| `identity_candidates.py` | Lists bot and agent accounts waiting to be classified |
| `levels_new.py` | Copies a maturity model you can edit and run with `--levels` |
| `calibration_corpus.py` | Diffs a run of the calibration corpus clones against their expected-state files and reports the conditions that do not discriminate |
| `base_baseline.sh`, `gitlab_gate.sh` | The gate's baseline comparison and its GitLab CI job script |

## Tests

Each script has a test in `skills/assess/tests/`, which maps every test file to the code
it pins. Run the suite with `make test-assess` from the repository root.
