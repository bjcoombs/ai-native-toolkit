# Spec: porcelain repo-list normalizer (CLI class)

## Provenance

Derived from this repo's real, completed `ghsync --porcelain` capability and its
behavioural contract in `tests/test_ghsync_porcelain.py`. The `--porcelain` flag
exists so downstream tooling (e.g. `/ghreport`) can parse the discovered repo
list off stdout: with it, stdout carries *only* repo names (one per line, no
prefix, no progress chatter) and every informational line goes to stderr. This
fixture reimplements that stdout/stderr contract as a small, dependency-free CLI
so the canary harness can drive it cold with just `python3` (no `gh`, no network).

## What the tool does

`porcelain.py` reads repo records on stdin and emits a clean, parseable repo
list on stdout.

- **Input (stdin):** one record per line, `name<TAB>archived`, where `archived`
  is `true` or `false`. Blank lines and `#`-comment lines are ignored.
- **Output (stdout):** the names of non-archived repos, de-duplicated and sorted
  ascending, one per line, nothing else.
- **Output (stderr):** a single summary line `discovered <N> repos`.
- **Exit codes:** `0` on success; `2` on a usage error (unknown flag or a
  malformed record).

## Class and tier rationale

CLI/tool class. Per the PRD tier defaults, CLI/tool is tier-1: exit-code +
output on real input, fully verifiable by a cold agent. There is no perceptual
residue, so there are no tier-3 criteria - this fixture is expected to certify
`PASS` end-to-end, which is exactly what disarms a refuse-everything gate
(canary criterion 2).

## Success criteria

- Given `input.txt`, exit code is `0`.
- Given `input.txt`, stdout is byte-identical to `expected_stdout.txt`
  (`alpha`, `mid`, `zebra`, one per line - archived and duplicate filtered).
- An unknown flag exits non-zero with a usage message on stderr.
