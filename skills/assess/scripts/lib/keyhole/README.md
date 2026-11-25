# lib/keyhole

The signals that ask whether an agent can work in the repository through a keyhole: from
one file, without the whole history in its head. They cover files that only grow, markers
nobody acts on, ownership that has drifted from the code, hidden coupling, where
understanding lives, and whether the running system is visible.

## Where to start

`keyhole_signals.py` is the entry point. It takes the outputs of the other modules and
builds the five run-context blocks (`behaviour`, `documentation`, `understanding`,
`runtime`, `structure`) and the named findings. It changes with almost every new signal.
Its long reference is `lib/docs/keyhole_signals.md`.

| Signal | Module | Long reference |
|---|---|---|
| Files that only ever grow | `accretion_ratchet` | - |
| TODO, deprecation, suppression and disabled-test markers | `promissory_markers`, with `python_regions` for Python strings and comments | `lib/docs/promissory_markers.md` |
| Declared ownership against where code lives | `structure_drift` | - |
| Static imports against files that change together | `coupling_analysis` | - |
| Where understanding lives, and how fast code moves | `understanding_analysis` | - |
| Dead code, tests that run, and runtime legibility | `liveness_scan` | `lib/docs/liveness_scan.md` |
| Telemetry the repository emits | `instrumentation_scan` | - |
| What kind of repository this is | `archetype` | - |
| Extra analysis to offer for Dart and JVM projects | `dart_capabilities`, `jvm_capabilities` | - |

`promissory_markers` and `accretion_ratchet` also run as scripts; run the file in this
folder, not the old path under `lib/`.

The package list and the shared rules are in [lib/README.md](../README.md).
