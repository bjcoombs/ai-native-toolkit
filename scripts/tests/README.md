# scripts/tests

The test suite for the `scripts/` package. Run it with `make test-scripts` from the
repository root. It is a required check (`scripts/ pytest`) with its own coverage floor.

The tests for the acceptance-contract gates in `scripts/contract/` live elsewhere, in the
repository's [tests/contract/](../../tests/README.md), and run in the plugin contract suite.

| Test file | What it pins |
|---|---|
| `test_floor_check.py` | `floor_check.py`: marker removal and clause checks |
| `test_floor_check_spec.py` | `floor_check.py` against the rules in the Tokens and Markers sections of `FLOOR.md` |
| `test_floor_text_oracle.py` | `FLOOR.md` itself, read from its own text rather than the script's constants |
| `test_floor_base_classify.py` | The path filter classifies files with the base branch's copy of `floor_check.py` |
| `test_floor_pin_exempt.py` | The exemption that lets a Dependabot pin bump skip sign-off |
| `test_floor_anchor.py` | `floor_anchor.py` helpers: messages, summaries and required checks |
| `test_floor_anchor_protection.py` | How `floor_anchor.py` reads branch protection |
| `test_fold_changes.py` | `fold_changes.py`: the version notes and the release fold |
| `test_transform.py` | `transform_skill.py` building blocks |
| `test_integration.py` | A full standalone build, then the contents of each ZIP |
| `test_cli_source.py` | The plugin `SKILL.md` sources before the build transforms them |

`fixtures/` holds the inputs: the floor role trees and a before-and-after tree for the fold.
