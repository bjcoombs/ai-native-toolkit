# Acceptance contract: Lane Runner (interactive class)

Frozen contract for the working interactive fixture. Tier-1 criteria are
drivable headlessly and expected to PASS; the single tier-3 criterion is
perceptual and human-mandatory, so the run passes tier-1 and then **stalls at
tier-3 escalation** (it does not certify until the operator's sign-off is
recorded). This within-class pass/stall against jet-fighters' tier-1 failure is
canary criterion 2b.

Per PRD B3 an interactive contract must contain at least one tier-3 criterion or
freeze refuses; LR5 satisfies that structural floor.

Drive surface: `window.__canary.getState()` -> `{phase, lane, score, tick}`;
input sent as DOM `keydown` events on `window`.

```yaml
class: interactive
criteria:
  - id: LR1
    tier: 1
    action: "Launch build/index.html; wait for load and read window.__canary.getState()."
    observation: "Canvas renders non-blank content and getState() returns {phase:'READY', lane in {0,1,2}, score:0}."
  - id: LR2
    tier: 1
    action: "Dispatch a Space keydown on window, then read getState().phase."
    observation: "phase transitions from 'READY' to 'PLAYING'."
  - id: LR3
    tier: 1
    action: "Record lane, dispatch ArrowUp (then ArrowDown) keydown events, reading lane after each."
    observation: "lane moves to an adjacent lane in {0,1,2} and is clamped at the ends - the runner responds to input."
  - id: LR4
    tier: 1
    action: "With phase PLAYING, record score, dispatch an 'f' keydown, read score again."
    observation: "score increments by 1 per fire."
  - id: LR5
    tier: 3
    action: "Operator launches the build and plays it briefly."
    observation: "The lanes, runner, and motion read and feel like the intended toy - responsive, legible, no visual glitches. Human-mandatory; recorded as observed output in the completion record."
```
