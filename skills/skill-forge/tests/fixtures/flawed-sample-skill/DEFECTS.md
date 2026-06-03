# Answer key for flawed-sample-skill

If a lens does not surface its row's defect during a forge, the panel - not the fixture - is at fault.

This fixture plants exactly one defect per judge lens. A correctly calibrated
panel catches all five. A lens that misses its own row indicates the lens is
broken, since the fixture is a known-good calibration target.

The fixture calibrates **both detection and severity**. The five per-lens
defects below test detection (does the lens find its defect?). The
severity-calibration cases that follow test judgement (does the lens rate a
defect at the *right* severity, and does it stay quiet on things that are fine?).
A lens that catches every planted defect but rates them all HIGH is miscalibrated,
and a lens that flags the clean-pass or near-miss case is over-firing.

## Per-lens planted defects (detection)

| Lens | Planted defect | Location | Expected severity |
|------|----------------|----------|-------------------|
| Fidelity | Skill states its purpose is a conventional-commit message (`type(scope): subject`), but the step tells the agent to write a flowing plain-prose paragraph instead, contradicting that format | SKILL.md step 3 | HIGH |
| Adversarial | Domain-native rationalization escape: "keep the subject under 50 characters, unless that would lose important meaning" lets the agent talk itself out of the limit | SKILL.md step 4 | MEDIUM |
| Compression | Bloated denormalized training knowledge: a multi-sentence explanation of what semver is and how ordering works, adding length without instruction | SKILL.md intro paragraph (the semver explanation) | MEDIUM |
| Usability | Assumes unestablished context: step 5 says to append "the matching tracker reference", but the skill never establishes which tracker, where the reference comes from, or has the agent gather it, so a fresh agent has no input to act on and gets stuck | SKILL.md step 5 | MEDIUM |
| Trigger/routing | `description` has no TRIGGER clause and is over-broad ("any kind of text, message, or document"), so it would over-fire on unrelated prompts | SKILL.md frontmatter `description` | HIGH |

## Severity-calibration cases (judgement)

These four cases live in the `## Formatting notes` section. They calibrate how
a lens *rates* what it finds, and whether it stays quiet on a non-defect. Getting
the severity wrong (or firing on a clean case) is a calibration failure even when
detection is perfect.

| Case | What it is | Location | Owning lens | Expected outcome |
|------|------------|----------|-------------|------------------|
| Borderline-LOW | Minor wording ambiguity: "capitalize the subject appropriately for the project's house style" is slightly vague but the intent is recoverable and low-impact | Formatting notes, bullet 1 | Usability | Rate **LOW** - noted, does not fail a case. Rating it MED/HIGH is over-severity. |
| Borderline-MED | Soft rationalization risk: "add a list of the affected files ... when it seems helpful" is a mild discretionary escape, weaker than the step-4 HIGH-adjacent escape | Formatting notes, bullet 2 | Adversarial | Rate **MED** - a real but moderate escape. Rating it HIGH is over-severity; missing it is under-detection. |
| Clean-pass | No defect: "always wrap any body text at 72 columns" is unambiguous, correct, and complete | Formatting notes, bullet 4 | (any) | **No finding.** A lens that flags this is over-firing. |
| Near-miss | Looks problematic but is fine: "use `fix` ... unless the diff only touches files under `docs/`, in which case use `docs`" reads like a soft "unless" escape, but the condition is objective and fully specified - not a rationalization hole | Formatting notes, bullet 3 | Adversarial | **No finding.** The "unless" is deterministic, not discretionary. A lens that flags every "unless" is over-firing. |
