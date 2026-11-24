# scripts

Repository tooling. Nothing here ships inside the plugin. The folder holds three things:
the floor checks, the acceptance-contract gates, and the build for the standalone skill
ZIPs. It is its own Python package, with its own `pyproject.toml`, lock file and test suite.

## Floor core: do not edit without sign-off

Two files here belong to the floor core, the set listed in `FLOOR_CORE_PATHS` in
`floor_check.py`. The other two members are `FLOOR.md` and `.github/workflows/floor.yml`.

| File | What it does |
|---|---|
| `floor_check.py` | Fails a pull request that removes a floor marker or gate call, weakens a clause in [FLOOR.md](../FLOOR.md), or changes a floor-core file without sign-off |
| `floor_anchor.py` | Fails unless the floor checks are still required on `main` and the `floor-signoff` environment still names the owner as reviewer |

A change to either file takes effect only with the maintainer's out-of-band
`floor-signoff` approval, which CI requests. An automated step, a marathon teammate or a
fix loop may propose such a change but must not apply it. [FLOOR.md](../FLOOR.md) states
the rule, and [the external-anchor proof](../docs/floor-anchor-proof.md) explains why the
checks cannot be switched off quietly.

## Gate code and canaries

The floor protects these too. A change here must pass the `canary suite (semantic layer)`
check, not a sign-off.

- `contract/` - the acceptance-contract gates. Start with `validate_completion.py`: it is
  the one component that decides whether a run may claim PASS. The others feed it:
  `start_gate.py` and `complete_gate.py` open and close a run, `freeze.py` freezes the
  contract, `spawn_verifier.py` is the only code that writes verifier results, and
  `verifier.py` and `tiers.py` hold the logic it calls. `contract_location.py` says where
  the per-run files live. `readiness_check_prompt.md` is the fixed prompt for the readiness
  check.
- `canaries/` - `run_canaries.py` runs the real gates against the fixtures in
  [tests/canaries/](../tests/canaries/README.md). `drive_interactive.mjs` drives the
  interactive fixtures in a headless browser.

## Release and build tools

| File | What it does |
|---|---|
| `fold_changes.py` | Folds the pending version notes in `skills/assess/changes/` into each counter's history. The release PR runs it through `make fold` |
| `build-standalone-skills.sh` | Builds the standalone ZIPs into `dist/standalone-skills/` |
| `standalone_skill_config.py` | Which skills the build packs, and the text each one swaps in |
| `transform_skill.py` | Turns one plugin skill into a self-contained ZIP |

[The standalone skill pipeline](../docs/contributing/standalone-skills.md) explains the
markers and the vendoring.

## Tests

`tests/` pins every script above. Run it with `make test-scripts` from the repository
root. [tests/README.md](tests/README.md) maps each test file to the script it covers.
