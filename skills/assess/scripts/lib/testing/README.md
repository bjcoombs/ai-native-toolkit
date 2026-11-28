# lib/testing

The test signals: does the repository enforce a coverage threshold, which tests sit beside
which code, and how far to trust the tests of the riskiest files. Mutation testing itself
lives next door in `lib/test_pressure/`.

## Where to start

`coverage_gate.py` answers the main question: is a line-coverage threshold configured,
and does CI run it? Its long reference is `lib/docs/coverage_gate.md`. It
draws on the readers below.

| Question | Modules |
|---|---|
| Is a coverage threshold set, and where? | `coverage_gate`, `coverage_codecov`, `coverage_root_config`, `coverage_activation` |
| Does CI run it, and from which folder? | `coverage_ci`, `ci_workdir` |
| What does an existing coverage report say? | `coverage_report` |
| Which tests belong to which code? | `sibling_tests` (the one home for test-file naming rules), `import_credit` |
| Where should testing effort go? | `test_focus`, `untrusted_hotspot` |
| How does mutation testing cap the score? | `mutation_cap`, `mutation_refresh` |
| Which tests pin behaviour beyond coverage (fuzzing, property, network-isolated, snapshot)? | `behaviour_pins`, `behaviour_pins_manifests` |

Each module's docstring says what it reads and what it returns. All of them only read: none
runs the tests or a coverage tool.

The package list and the shared rules are in [lib/README.md](../README.md).
