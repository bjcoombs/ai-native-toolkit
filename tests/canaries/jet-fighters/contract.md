# Acceptance contract: Jet Fighters (interactive/visual class)

Authored from `prd.md` (the verbatim jet-fighters v1 PRD). This is the frozen
contract the cold exit verifier executes against `build/` (the broken build) and
against `null_artifact/` (the freeze-time kill-test).

The class is `interactive`, so the machine tiers are thin by design (PRD
Technical Context, "honest narrowness"): tier-1 covers what a cold agent can
drive and observe headlessly - the game must actually *start, move, and score*.
Tier-3 covers the perceptual residue (visual fidelity, game-feel) a cold agent
structurally cannot judge; per PRD B3 an interactive contract MUST carry at
least one tier-3 criterion or freeze refuses.

Discrimination this fixture establishes: the broken build renders a full-looking
scope (so process signals read "done") but never wires input or ticks the
simulation, so criteria JF2, JF3, JF4 FAIL at cold exit. Those tier-1 failures
must be present in the completion record - the build is *detected*, not merely
filed PARTIAL because the class is interactive (canary criterion 1).

Drive surface: the build exposes `window.__canary.getState()` returning a JSON
snapshot `{phase, score, launcher:{lane,lives}, jets:[{lane,col}], missile, tick}`.
Input is sent as standard DOM keyboard events. A correct build subscribes to
them and advances a fixed-timestep loop; the broken build does neither.

```yaml
class: interactive
criteria:
  - id: JF1
    tier: 1
    action: "Launch build/index.html; wait for load, then read window.__canary.getState()."
    observation: "The scope canvas renders non-blank content and getState() returns a readable state object with a numeric score and launcher.lane in {0,1,2}."
  - id: JF2
    tier: 1
    action: "With the game running, observe getState().jets positions across several successive animation frames (>= 1 second of wall time)."
    observation: "At least one jet's (lane,col) changes over time - the squadron advances toward the missile station. Position is not identical across all observations."
  - id: JF3
    tier: 1
    action: "Record launcher.lane, dispatch an ArrowUp (or ArrowDown) keydown on window, then read launcher.lane again."
    observation: "launcher.lane changes to an adjacent lane in {0,1,2} - the launcher lever moves between lanes in response to input."
  - id: JF4
    tier: 1
    action: "Aim the launcher at a jet's lane, dispatch a Space keydown to fire, and let the missile travel to impact."
    observation: "A missile appears in flight (getState().missile becomes non-null) and, on impact with a jet, score increases per the 3/2/1 distance ruler."
  - id: JF5
    tier: 3
    action: "Operator launches the build and plays it, comparing against assets/reference/device-front-gameplay.jpg from the source repo."
    observation: "The two-colour VFD look, silkscreen overlay, sprite placement, and game-feel are recognisably the 1979 Jet Fighters unit. Human-mandatory; recorded via the operator's observed output pasted into the completion record."
```
