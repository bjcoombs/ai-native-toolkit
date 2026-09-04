# Modernization Programme

Start with [intent.md](intent.md) for the problem, the decisions and the sequence, and [probes.md](probes.md) for the three probes that settle D5.

| WS | Workstream | Tag | Depends on | Spec |
|----|------------|-----|------------|------|
| WS0 | Floor | `modernization-floor` | Nothing | [floor/spec.md](floor/spec.md) |
| WS1 | Layout | `modernization-layout` | WS0 merged; Probes recorded | [layout/spec.md](layout/spec.md) |
| WS2 | Naming | `modernization-naming` | Lands inside PR1 | [naming/spec.md](naming/spec.md) |
| WS3 | Frontmatter and agents | `modernization-frontmatter` | WS1 | [frontmatter/spec.md](frontmatter/spec.md) |
| WS4 | Disclosure and clarity | `modernization-disclosure` | WS1; huddle transfer set (R6 prerequisite) | [disclosure/spec.md](disclosure/spec.md) |
| WS5 | Knowledge files | `modernization-knowledge` | WS1 | [knowledge/spec.md](knowledge/spec.md) |
| WS6 | Guardrails | `modernization-guardrails` | WS1 | [guardrails/spec.md](guardrails/spec.md) |
| WS7 | Distribution | `modernization-distribution` | WS1 | [distribution/spec.md](distribution/spec.md) |
| WS8 | Behaviour | `modernization-behaviour` | WS4 | [behaviour/spec.md](behaviour/spec.md) |
| WS9 | Evals | `modernization-evals` | WS4, WS8 | [evals/spec.md](evals/spec.md) |
| WS10 | Sunset | `modernization-sunset` | All, next MAJOR | [sunset/spec.md](sunset/spec.md) |

Every spec carries the same eight sections: Intent, Current state, Design, Requirements, Verification, Breadcrumbs, Rollback, Sequencing.
