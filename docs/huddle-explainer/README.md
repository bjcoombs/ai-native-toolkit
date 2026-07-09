# The `/huddle` explainer: how it got made

A roughly two-minute narrated explainer for `/huddle`, built to show someone unfamiliar with the skill why structured multi-perspective deliberation is a different thing from asking one model once. The visual asset was built in a Claude Design project and is published below as a static export; this folder carries that export, the narration audio, and the production story, because the story turned out to be a fitting demonstration of the thing the video is arguing for. Back to the [Map of Content](../index.md).

## The asset

- **Interactive**: [Huddle Visualization](https://bjcoombs.github.io/ai-native-toolkit/huddle-explainer/visualization.html) - an animated timeline built in Claude Design and published as a self-contained export ([`visualization.html`](./visualization.html)), showing an open-ended problem move through framing, Fibonacci sizing, the five hats in sequence, forced cross-talk, a bounded completeness-critic pass, and a verdict that keeps its dissent attached rather than smoothing it away. Claude Design projects themselves are workspace-private with no public-link option, so this export is what makes the visual reachable outside the team. Its own play/pause/scrub transport also drives [`narration.mp3`](./narration.mp3) - the export has no exposed player API, so a small script tracks the two on-screen time readouts it does expose and keeps the audio's `currentTime` locked to whichever is smaller, self-healing after the export's own unpack step rewrites the page body.
- **Audio**: [`narration.mp3`](./narration.mp3) - a 120-second narration track built to the video's exact timeline. [`narration-script.md`](./narration-script.md) has the full cue-by-cue script with timestamps and the reasoning behind every deviation from the on-screen captions.

## Why this write-up exists

Every real fix in producing this asset had the same shape as the thing it's explaining: a fluent-sounding first pass that missed a load-bearing detail, caught only by checking it against something that could actually verify it - the source file for a mechanic, measured timing for a pace, a human ear for a pronunciation. That is worth documenting on its own merits, not as a victory lap.

## What the first cut missed

The initial animation nailed the surface - the color-coded hats, the Fibonacci team-size sweep, a verdict frame - but missed the part of `/huddle` that actually makes it a system rather than a fixed cast of six characters: team size, who is on the team, and the hat sequence are all chosen fresh, per problem, by the chair, not a script that runs the same way every time. [`skills/huddle/SKILL.md`](../../skills/huddle/SKILL.md) names all three explicitly:

- Team size: "Fibonacci numbers scale naturally with complexity" (1 / 2 / 3 / 5 / 8+).
- Who's seated: "Professional lenses... that create productive tension for THIS topic. You know what expertise fits - choose what creates the most useful disagreement."
- The sequence: "Quick: Red only... Simple: White → Black... Moderate: White → Red → Black → Yellow → Green... Adapt the sequence to the topic. These are guides, not straitjackets."

The fix wasn't a rebuild, it was one visual beat: at the "convene" moment, show a pool of ten candidate experts with only five stepping forward and getting seated, so the selection is dramatized instead of asserted. That single beat proves both the team-size and expert-selection claims at once, without adding runtime.

## What the narration pass caught

Writing the script surfaced a second gap: roughly 32 seconds of the video - the six-hats legend, and the entire closing act - had real on-screen content and zero narration. New cues went in only where a scene genuinely had no commentary layer yet, reusing the pattern the file already establishes: a caption always adds something the primary on-screen text doesn't already say, never a duplicate readout of it. That rule is also why several proposed cues were deliberately left out (see [`narration-script.md`](./narration-script.md)) - narrating text that's already the hero content on screen would have been noise, not clarity.

Generating the audio then surfaced two defects that no amount of reading the script would have caught:

**A pacing collision.** Three lines were written assuming a spoken pace of roughly 2.5-3.3 words per second. The voice's actual realized pace across every other cue was a consistent ~2.0-2.1 words per second, so those three ran long enough to overlap the start of the next line by up to 1.6 seconds. The fix was trimming the text to the measured pace, not maxing out the playback-speed setting - for the worst offender, even the fastest allowed speed setting wouldn't have closed the gap.

**A pronunciation problem only a human ear caught.** "Gut" rendered as something closer to "gute," and later, "a simpler call" blended into what sounded like one word. Neither showed up on a transcription round-trip: speech-to-text just normalizes back to the intended word regardless of how it was actually pronounced, so that check has no power over this specific failure mode. The fix was empirical - try a phonetic respelling ("gutt," forcing the short vowel) for the first, a rephrase that removed the "-er"-plus-hard-consonant collision for the second, then have a person listen and confirm before locking either one in.

## The pattern, stated plainly

Every fix here came from the same move: don't trust the fluent-sounding first pass, check it against the thing that can actually verify it. That is the entire argument `/huddle` makes about deliberation. It held up under its own production.
