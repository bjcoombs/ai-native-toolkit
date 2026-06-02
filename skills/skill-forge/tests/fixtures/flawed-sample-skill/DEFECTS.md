# Answer key for flawed-sample-skill

If a lens does not surface its row's defect during a forge, the panel - not the fixture - is at fault.

This fixture plants exactly one defect per judge lens. A correctly calibrated
panel catches all five. A lens that misses its own row indicates the lens is
broken, since the fixture is a known-good calibration target.

| Lens | Planted defect | Location | Expected severity |
|------|----------------|----------|-------------------|
| Fidelity | Skill states its purpose is a conventional-commit message (`type(scope): subject`), but the step tells the agent to write a flowing plain-prose paragraph instead, contradicting that format | SKILL.md step 3 | HIGH |
| Adversarial | Rationalization escape: "follow TDD ... unless it seems unnecessary" lets the agent talk itself out of the instruction | SKILL.md step 5 | MEDIUM |
| Compression | Bloated denormalized training knowledge: a multi-sentence explanation of what semver is and how ordering works, adding length without instruction | SKILL.md intro paragraph (the semver explanation) | MEDIUM |
| Usability | Ordering defect: step 4 depends on the character limit "decided in step 6", so a literal in-order reader cannot apply step 4 yet | SKILL.md step 4 (forward reference to step 6) | MEDIUM |
| Trigger/routing | `description` has no TRIGGER clause and is over-broad ("any kind of text, message, or document"), so it would over-fire on unrelated prompts | SKILL.md frontmatter `description` | HIGH |
