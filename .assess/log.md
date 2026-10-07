# Assess Log

## 2026-05-31 (v1.18.2)

- **Files scored:** 58
- **AI Readiness:** 6.0 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 0 graduated, 0 regressed, 10 new, 0 persistent
- **Top action:** Escape the bare % in complexity-treemap.py's --test-pressure argparse help so the treemap runs on Python 3.14

[Full report](./assess-report.md)

---
## 2026-06-01 (v1.19.2)

- **Files scored:** 58
- **AI Readiness:** 6.0 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 0 graduated, 1 regressed, 0 new, 9 persistent
- **Top action:** Add a coverage step with a patch-coverage floor, then mutation testing on the deterministic core

[Full report](./assess-report.md)

---
## 2026-06-01 (v1.23.0)

- **Files scored:** 69
- **AI Readiness:** 6.0 / 8 (Advanced / Engineered)
- **Instructions grade:** A
- **Hotspot transitions:** 2 graduated, 1 regressed, 2 new, 7 persistent
- **Top action:** Document the assess_core -> scripts/lib seam (add skills/assess/scripts/lib/README.md) so the hidden-coupling cohesion is owned, not just observed

[Full report](./assess-report.md)

---
## 2026-06-04 (v1.35.0)

- **Files scored:** 72
- **AI Readiness:** 7.0 / 8 (AI-Native)
- **Instructions grade:** A
- **Hotspot transitions:** 2 graduated, 3 regressed, 2 new, 5 persistent
- **Top action:** Extend the co-change seam map (skills/assess/scripts/lib/README.md) to the full scripts <-> scripts/tests <-> skills/assess span so the coupling is owned, not just observed

[Full report](./assess-report.md)

---
## 2026-06-19 (v1.46.2)

- **Files scored:** 92
- **AI Readiness:** 8.0 / 8 (AI-Native (Optimized))
- **Instructions grade:** A
- **Hotspot transitions:** 2 graduated, 5 regressed, 2 new, 3 persistent
- **Top action:** Refactor down assess_core.py (only grows: +1089 net, ~7% deletion) behind its existing tests, then action the stale promissory marker at line 680

[Full report](./assess-report.md)

---
<!-- assess:run_id=20260919114544-b4104782 artifact_schema_version=1.1.0 -->
## 2026-09-19 (v1.87.0, run b4104782)

- **Files scored:** 168
- **AI Readiness:** 5.5 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 2 graduated, 8 regressed, 2 new, 0 persistent
- **Top action:** Request human review of scripts/tests/test_floor_anchor.py against FLOOR.md: tests and code arrived in the same commit

[Full report](./assess-report.md)

---
<!-- chain:a6e1017927b7a96a -->
<!-- assess:run_id=20261007120131-f520a0c5 artifact_schema_version=1.3.0 -->
## 2026-10-07 (v1.92.0, run f520a0c5)

- **Files scored:** 172
- **AI Readiness:** 5.5 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 1 graduated, 7 regressed, 1 new, 2 persistent
- **Top action:** Give each stale suppression in skills/assess/scripts/lib/doc_graph.py a stated reason or remove it

[Full report](./assess-report.md)

---
<!-- chain:91c10d55d1a1e66c -->
<!-- assess:run_id=20261007185833-e950cb1b artifact_schema_version=1.3.0 -->
## 2026-10-07 (v1.92.6, run e950cb1b)

- **Files scored:** 172
- **AI Readiness:** 6.0 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 0 graduated, 7 regressed, 0 new, 3 persistent
- **Top action:** Decide how FLOOR.md clause iii and floor_core_changed agree, then add the clause-derived floor tests (#410)

[Full report](./assess-report.md)

---
<!-- chain:2e10d3785b44fd79 -->
<!-- assess:run_id=20261007204013-4ace52ea artifact_schema_version=1.3.0 -->
## 2026-10-07 (v1.92.7, run 4ace52ea)

- **Files scored:** 176
- **AI Readiness:** 6.0 / 8 (Solid)
- **Instructions grade:** A
- **Hotspot transitions:** 1 graduated, 3 regressed, 0 restructured, 1 new, 6 persistent
- **Top action:** Decide how FLOOR.md clause iii and floor_core_changed agree, then add the clause-derived floor tests (#410)

[Full report](./assess-report.md)

---
<!-- chain:3287cc08dc4b7312 -->
