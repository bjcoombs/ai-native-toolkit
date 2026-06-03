# Design history

The plans and specs behind the skills, captured as they were built. These are historical design records - the shipped behaviour lives in the [skills](../../skills/README.md) and [commands](../../commands/README.md) themselves - but they document the reasoning and trade-offs that produced each feature. Back to the [Map of Content](../index.md).

## Plans

| Plan | Subject |
|------|---------|
| [Assess deterministic wiki foundation](./plans/2026-05-22-assess-deterministic-wiki.md) | The compounding `.assess/` wiki foundation |
| [Assess v1.5 real-use fixes](./plans/2026-05-22-assess-v1.5-real-use-fixes.md) | Feedback fixes from real-world use |
| [Standalone skill pipeline](./plans/2026-05-23-standalone-skill-pipeline.md) | The standalone-ZIP build pipeline |
| [Assess truth-pressure signals (read-side)](./plans/2026-05-27-assess-truth-pressure-signals.md) | Read-side truth-pressure foundation |
| [Huddle broadcast per recipient](./plans/2026-05-27-huddle-broadcast-per-recipient.md) | Team-mode broadcast incompatibility |
| [Huddle CLI team-mode regression](./plans/2026-05-27-huddle-cli-team-mode-regression.md) | CLI team-mode regression fix |
| [Assess dismiss false positives](./plans/2026-05-28-assess-dismiss-false-positives.md) | Suppressing false-positive findings |
| [Assess keyhole readiness](./plans/2026-05-29-assess-keyhole-readiness.md) | Structural legibility (keyhole) signals |
| [Assess write-side truth-pressure](./plans/2026-05-29-assess-write-side-truth-pressure.md) | Write-side verification signals |
| [Issues marathon + shared skills](./plans/2026-05-29-issues-marathon-shared-skills.md) | `/issues` marathon and shared marathon skills |
| [Assess dogfooded - baseline analysis](./plans/2026-05-31-assess-dogfooded-analysis.md) | Phase 0 baseline and seam analysis |
| [Assess dogfooded - PRD](./plans/2026-05-31-assess-dogfooded.md) | Teeth, a frozen harness, and decomposition |
| [skill-forge](./plans/2026-06-02-skill-forge.md) | Build the judge-panel skill-hardening harness in bootstrap order, forged by itself |

## Specs

| Spec | Subject |
|------|---------|
| [Issues marathon shared-skills design](./specs/2026-05-29-issues-marathon-shared-skills-design.md) | Design for `/issues` via shared marathon skills |
| [skill-forge design](./specs/2026-06-02-skill-forge-design.md) | Judge-panel skill-hardening harness, refined through its own process |
| [semantic-compress distillation design](./specs/2026-06-03-semantic-compress-distillation-design.md) | semantic-compress v2 holistic distillation - produce the smallest document that passes A/B behavioural equivalence |
| [instruction optimizer directive-clarity design](./specs/2026-06-03-instruction-optimizer-directive-clarity-design.md) | Directive-clarity transform - first A/B-validated member of the instruction cognitive-ergonomics family |
| [skill-forge hardening + A/B-extraction design](./specs/2026-06-03-skill-forge-hardening-and-ab-extraction-design.md) | Extract A/B-equivalence into a standalone library skill and apply five B1-B5 hardening changes to skill-forge |
