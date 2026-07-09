# Spec: Lane Runner (interactive class, working fixture)

## Purpose

A minimal, genuinely-playable interactive toy that serves as the **within-class
positive control** for the canary suite. It is in the same class as the broken
jet-fighters build (interactive/visual) but it actually works: input is wired and
the simulation runs. It passes its tier-1 criteria under a cold headless driver
and stalls only at its one tier-3 (perceptual) criterion.

Why it exists: jet-fighters proves a broken interactive build is caught
(tier-1 FAIL). On its own, that could be satisfied by a gate that refuses *every*
interactive product. This fixture closes that gap - a gate that actually drives
builds must pass this one's tier-1 criteria while failing jet-fighters'
(canary criterion 2b).

## What the toy does

Three horizontal lanes with a runner block. State machine: `READY` -> `PLAYING`.

- **Space** starts the game (`READY` -> `PLAYING`).
- **ArrowUp / ArrowDown** move the runner to an adjacent lane (clamped to 0..2).
- **f** scores a point while `PLAYING`.

The build renders on a canvas, wires the above input, and runs a fixed-cadence
loop that advances an internal tick counter (so a verifier can confirm the sim
is live rather than a frozen render).

## Drive surface

`window.__canary.getState()` returns `{phase, lane, score, tick}`. Input is sent
as standard DOM `keydown` events. A cold agent can drive every tier-1 criterion
headlessly.

## Class and tier rationale

Interactive/visual class. Tier-1 covers the drivable, observable mechanics
(start, move, score). Tier-3 covers the perceptual residue (does it read and
*feel* like the intended toy) that a cold agent structurally cannot judge - so
it escalates to the operator. Per PRD B3 the contract carries at least one tier-3
criterion; freeze would refuse otherwise.

## Success criteria

- Launch: canvas renders and `getState()` is readable, `phase === 'READY'`.
- Space -> `phase === 'PLAYING'`.
- ArrowUp/ArrowDown -> `lane` changes to an adjacent lane.
- `f` while PLAYING -> `score` increments.
- Tier-3: operator confirms the toy reads and feels right (human-mandatory).
