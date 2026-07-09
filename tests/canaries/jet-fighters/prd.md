# Jet Fighters - Browser Replica PRD (v1)

## Problem Statement

Recreate the 1979 Gakken "Jet Fighter" tabletop electronic game (model 81582, sold in the UK
by Computer Games Limited as "Jet Fighters") as a faithful browser game, working from video
and photos of an original CGL unit plus documented research. The finished game is open source
(MIT) and playable at a GitHub Pages URL directly from this repo.

## Technical Context

### The original hardware

- Tabletop VFD ("FL") game, red plastic case held sideways, round black "radar scope" screen
  window with white silkscreened overlay. Gakken's own 1979 LSI Invader game rotated 90
  degrees and re-themed: the invaders are jets attacking a coastal missile station.
- Screen overlay text: "COAST SIDE MISSILE STATION RADAR SIGHT SCREEN". Zone labels along the
  playfield: BATTLE SHIP ZONE (far side), JET FIGHTER FLYING ZONE (middle), MISSILE STATION
  ZONE (player side). A printed scoring ruler along one edge: 10 / 3 / 2 / 1 / G.
- Two-colour VFD: orange/amber jets and battleship, blue/cyan for the player side (missile
  shots, launcher) and the digital SCORE readout. Three launcher (life) silhouettes printed
  at the base of the missile station zone.
- Controls: blue push button (fire), spring-centred lever that snaps the launcher between
  THREE lane positions (top/middle/bottom of the player edge), rotary skill dial labelled
  1/2/3, and a power ON/OFF slide switch (rule: power-cycle to start a new game).

### Rules (from the unit's back label, owner testimony, and research)

1. Skill dial sets level 1 (easiest) to 3 (fastest). Power switch starts the game.
2. Jets advance in ranks across the screen from the battleship side toward the missile
   station, Invader-style: step, pause, step. As a squadron thins out, the survivors speed
   up; each cleared squadron respawns faster.
3. The player snaps between 3 lanes and fires missiles; a missile travels as a two-dot
   trail toward the far side, one missile in flight at a time.
4. Scoring by distance at the moment of impact, per the printed ruler: jets are worth
   3 / 2 / 1 (farther = more). The battleship crosses the far BATTLE SHIP ZONE at random
   moments (announced by a distinctive lower-pitch buzz) and is worth 10.
5. Jets fire rockets back down the lanes; a rocket that hits the player's launcher destroys
   it. The player has 3 launchers total.
6. Game ends when: the player reaches 199 points (win - score display is 2-3 digit VFD),
   OR all 3 launchers are destroyed by rockets, OR a jet reaches the G line and captures
   the launcher (instant game over).
7. Back-label instructions verbatim: "1. Set skill control to desired level. Slide power
   on/off switch to 'on'. 2. Move the Missile launcher lever up and down to aim at
   Jet-Fighter or Battleship, and press Missile launch button. 3. Game ends when you
   obtained 199 points or when all Missile Launchers are destroyed by Jet-Fighter's rockets
   or when Jet-Fighter captures your Missile Launcher. 4. To start new game simply slide
   switch to 'off' and then to 'on' again."

### Reference assets (committed in `assets/reference/`, relative to the repo root)

- `gameplay-audio.m4a` - full audio recording of the real unit playing (~130 s). Source for
  all sound effects: missile fire, jet march/buzz, battleship low buzz, explosion, game over.
- `device-front-lit.jpg`, `device-front-gameplay.jpg` - full unit with screen active.
- `screen-closeup-gameplay.jpg`, `screen-overlay-closeup.jpg` - playfield and silkscreen detail.
- `back-instructions-label.jpg` - the CGL instruction label.

### Stack (decided)

- TypeScript + HTML5 canvas + Web Audio. Zero runtime dependencies.
- Vite for dev/build. Vitest for unit tests. GitHub Actions deploys `main` to GitHub Pages.
- Game logic is pure and deterministic (seedable RNG), fully separated from rendering,
  input, and audio so it is unit-testable.

## Solution Requirements

### R1. Project scaffold and deploy pipeline

- Vite + TypeScript project; `npm run dev`, `npm run build`, `npm test` (Vitest), `npm run lint`.
- GitHub Actions workflow: on push to `main`, run lint + tests + build, deploy to GitHub Pages.
- README: playable link, screenshots, control reference, credits to Gakken/CGL, MIT license note.

### R2. Core game logic (pure TypeScript module, no DOM)

- Discrete tick simulation on a fixed grid: 3 lanes x N distance columns (calibrate N from
  the video footage; expected 5-7 columns between battleship zone and G line).
- Entities: jet squadron (ranks that step toward the player, thin-out speed-up, respawn
  faster each wave), battleship (random far-zone crossings), player launcher (3 lanes),
  player missile (one in flight), jet rockets (travel down a lane toward the player).
- Scoring: jets 3/2/1 by distance column band, battleship 10, win at 199 (score caps at 199).
- Lives: 3 launchers; rocket hit destroys one; jet reaching G = instant game over.
- Skill levels 1/2/3 scale tick cadence and aggression.
- Deterministic given a seed; full Vitest coverage of stepping, collision, scoring, and
  end conditions.

### R3. VFD screen renderer (canvas)

- Black scope background; two-colour VFD look: amber/orange jets + battleship, cyan missile,
  launcher, and 7-segment style SCORE readout; subtle glow/bloom, faint ghost segments
  (unlit VFD cells barely visible) for authenticity.
- White silkscreen layer reproducing the overlay: playfield border, zone labels, the
  10/3/2/1/G ruler with dotted ticks, and the "COAST SIDE MISSILE STATION RADAR SIGHT
  SCREEN" arc text, matched to the reference photos.
- Sprite shapes traced from the reference footage (jet, battleship, missile dots, launcher,
  explosion burst).

### R4. Case and controls UI

- Full-page rendering of the red tabletop unit (SVG/CSS): case silhouette, blue JET FIGHTERS
  label, round screen window over the canvas, textured back panel feel.
- Working on-case controls wired to the game: blue fire button (press animation), launcher
  lever (snaps between 3 positions, springs to centre visual), skill dial (1/2/3), power
  ON/OFF slide switch which starts/resets the game exactly like the original (rule 4).
- Responsive: fits desktop and mobile landscape; controls are touch targets on mobile.

### R5. Input

- Keyboard: Arrow Up/Down or W/S to move lanes, Space/Enter to fire, keys for power and
  skill; mappings shown in the README and a small on-page help hint.
- Pointer/touch: tap the on-case controls; drag or tap zones for lane movement on mobile.
- All input flows through one intent layer consumed by the game logic.

### R6. Audio

All effects are mathematical Web Audio syntheses (oscillators/envelopes) - no sampled
clips ship. The reference recordings are ground truth to imitate: measure fundamentals,
envelopes, and note sequences from them (the original is a simple piezo speaker, so
square waves are the authentic waveform).

Owner-confirmed sound semantics (verified against the real unit):

- Missile fire: a single beep. A missile hitting a jet/battleship produces the SAME beep.
- Player's launcher hit by a rocket: two beeps on the first hit, three beeps on the
  second hit, and on the third hit the full LOSS sound (game over).
- Battleship crossing: distinctive lower-pitch buzz. Jets: march/step buzz.
- WIN at 199 points: the melodic jingle heard at the tail (~120.4 s) of
  `assets/reference/gameplay-audio.m4a` - the owner confirmed that recording ended in a
  win. Transcribe and synthesize that melody for `playWin()`.
- LOSS (third hit): the full loss sound is captured in `assets/reference/loss-audio.m4a`
  (~89 s recording; the loss sound is near its end). The two-beep and three-beep warning
  patterns use the same notes as the loss sound's opening - extract them from it.
- Paths above are relative to the repo root. Web Audio playback keyed to game events;
  mute toggle.

### R7. Integration and game states

- State machine: OFF (dark screen) -> PLAYING -> GAME_OVER / WIN(199), driven by the power
  switch per the original's power-cycle-to-restart behaviour.
- Wire logic ticks to render frames (fixed-timestep update, rAF render) and audio events.

## Success Criteria

- `npm test` green; logic module covered by deterministic unit tests.
- Playable at the GitHub Pages URL on desktop and mobile.
- Visual match: side-by-side with `device-front-gameplay.jpg` the screen layout, sprite
  placement, colours, and silkscreen are recognisably the same game.
- Rules match this PRD exactly (scoring bands, 199 win, 3 launchers, capture line, skill
  levels, power-cycle restart).
- Sounds recognisably match the reference recording.
- No runtime dependencies; repo installs and runs with `npm install && npm run dev`.
