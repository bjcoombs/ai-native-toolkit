# `lib/liveness_scan.py`

The full reference for `skills/assess/scripts/lib/liveness_scan.py`, whose module docstring holds the summary.

Layer 1 liveness inputs, three tiers:
- Dead-code tier: runs a language-appropriate static dead-code tool (vulture, ts-prune,
  staticcheck, etc.) to flag candidate-dead exports within the repo boundary.
  JavaScript and TypeScript share one choice, made by the dominant language of the
  in-scope files (`.ts`/`.tsx`/`.mts`/`.cts` against `.js`/`.jsx`/`.mjs`/`.cjs`; a
  scoped run counts only the scope's files); the losing
  language gets one `not_applicable` entry naming its unanalysed file count. ts-prune
  also needs a root `tsconfig.json`; without one it is recorded `not_applicable` and
  not run. A JavaScript-dominant repo with no `knip` on PATH
  records `javascript` / `knip` / `honest_degrade`, so "not analysed" never reads as
  "0 candidates".
- Observability tier: scores three rungs - instrumented (telemetry emitted), discoverable
  (runbook present), reachable (agent has an invokable path to runtime state). The
  reachability rung decides the Layer 1 score. Rung 1 comes from
  `instrumentation_scan.py` (#823): a telemetry dependency in a manifest, matched as a
  whole name; a standard-library logger or logging facade in use in production source;
  or a committed collector config. `instrumented.kinds` records which of `dependency`,
  `code` and `config` matched. Only production source counts (`path_classes.py`), and a
  doc under a test or fixture path is never a runbook. On-call in a doc or directory name
  needs a word or path-segment boundary, so `function-call-in-default-argument.md` and
  `non-callable-init-subclass.md` are not runbooks.
- Capability-offer tier: delegates to `jvm_capabilities.py` to detect JVM/Maven build
  systems and report, per analysis capability, whether a serving tool is already
  configured, could be run/installed in-session, or honest-degrades with a named
  candidate. Surfaced so a non-enumerated ecosystem proposes a tool rather than
  silently reading "absent". Also delegates to `dart_capabilities.py`, whose
  liveness entry lands in `dead_code.tools` as `dart` / `honest_degrade`.
