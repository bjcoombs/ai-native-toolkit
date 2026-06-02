# Answer key for flawed-sample-skill

If a lens does not surface its row's defect during a forge, the panel - not the fixture - is at fault.

This fixture plants exactly one defect per judge lens. A correctly calibrated
panel catches all five. A lens that misses its own row indicates the lens is
broken, since the fixture is a known-good calibration target.

| Lens | Planted defect | Location | Expected severity |
|------|----------------|----------|-------------------|
| Fidelity | Skill states its purpose is a conventional-commit message (`type(scope): subject`), but the step tells the agent to write a flowing plain-prose paragraph instead, contradicting that format | SKILL.md step 3 | HIGH |
| Adversarial | Domain-native rationalization escape: "keep the subject under 50 characters, unless that would lose important meaning" lets the agent talk itself out of the limit | SKILL.md step 4 | MEDIUM |
| Compression | Bloated denormalized training knowledge: a multi-sentence explanation of what semver is and how ordering works, adding length without instruction | SKILL.md intro paragraph (the semver explanation) | MEDIUM |
| Usability | Assumes unestablished context: step 5 says to append "the matching tracker reference", but the skill never establishes which tracker, where the reference comes from, or has the agent gather it, so a fresh agent has no input to act on and gets stuck | SKILL.md step 5 | MEDIUM |
| Trigger/routing | `description` has no TRIGGER clause and is over-broad ("any kind of text, message, or document"), so it would over-fire on unrelated prompts | SKILL.md frontmatter `description` | HIGH |
