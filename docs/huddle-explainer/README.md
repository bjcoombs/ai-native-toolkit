# How to build a narrated skill explainer

A repeatable pipeline for turning a skill's `SKILL.md` into a short narrated animation: Claude Code drives a Claude Design project through MCP, ElevenLabs supplies the narration, and Claude in Chrome publishes the result. This page documents the pipeline itself, not just the one example it produced. Back to the [Map of Content](../index.md).

Worked example: [Huddle Visualization](https://bjcoombs.github.io/ai-native-toolkit/huddle-explainer/visualization.html) (interactive export, synced to [`narration.mp3`](./narration.mp3); cue-by-cue script in [`narration-script.md`](./narration-script.md)), built from `/huddle`'s own [`skills/huddle/SKILL.md`](../../skills/huddle/SKILL.md).

## What you need

- **Claude Code**, run from this repo so it can read the target skill's `SKILL.md` directly - that file is the source of truth for what the animation has to prove, not a paraphrase of it.
- **A Claude Design project**, reachable from Claude Code via the DesignSync MCP (`/design-login` once per machine). Claude Code is the instigator throughout: it drafts the brief and the prompts, Claude Design builds and revises the animation, Claude Code reads the result back to check it against the skill file.
- **An ElevenLabs account** (the free tier is enough) with the ElevenLabs MCP configured, for narration.
- **Claude in Chrome** (browser automation), for the YouTube upload step and for verifying any published pages.
- **Somewhere to host a public URL.** Claude Design projects are workspace-private with no "anyone with the link" option, so a Design link alone can never be shared outside the team. Plan to export the design as standalone HTML and host that export somewhere with a real public URL (this repo uses GitHub Pages, serving `docs/` on `main`) from the start, rather than discovering the gap after the fact.

## The pipeline

### 1. Draft the brief from the skill file, not from memory

Read the target `SKILL.md` and pull out the specific claims that make it a system rather than a fixed script - the parts that change per invocation, not the parts that are always the same. For `/huddle` that meant three claims, each traceable to an exact quote:

- Team size: "Fibonacci numbers scale naturally with complexity."
- Who's seated: "Professional lenses... that create productive tension for THIS topic. You know what expertise fits."
- The sequence: "Adapt the sequence to the topic. These are guides, not straitjackets."

Write the storyboard so every beat maps back to one of these quotes. A brief built from a vibe of what the skill does, rather than its actual text, will nail the surface (colors, layout, a verdict frame) and miss the mechanic.

### 2. Build and iterate the animation via DesignSync MCP

Have Claude Code push the brief to Claude Design and iterate through the MCP - `list_files` / `get_file` to read back what exists, `finalize_plan` / `write_files` to push changes. Review each pass against the brief's quotes, specifically:

- **Does it dramatize the dynamic parts, or just the static ones?** A team-sizing sweep and color-coded hats are static-parts wins. Proving that the team and sequence are chosen fresh each time needs an explicit beat - showing a pool of candidates with only a subset stepping forward and getting seated proved both the team-size and expert-selection claims at once, without a rebuild.
- **Does every caption add something the screen doesn't already say?** A caption that re-reads the on-screen text is noise, not narration-in-waiting; catching this now is cheaper than catching it during the narration pass.

### 3. Write narration to the animation's exact timeline

Configure the ElevenLabs MCP, then write cues against the animation's actual on-screen timeline, not a free-running script. Only narrate scenes that don't already carry their content on screen - check the animation for any stretch of real on-screen content with zero commentary layer (a legend, a closing act) and cue those specifically, rather than narrating start-to-finish by default.

### 4. Generate the audio, then correct it by measurement and by ear

Generate each cue, then check two things a script read-through can't catch:

- **Pacing.** Measure the voice's *actual* realized pace from the generated audio (words per second), not an assumed one. A cue timed to an assumed pace that runs long will overlap the next cue's start - fix by shortening the text to the measured pace, not by raising playback speed (for a large enough overrun, even the fastest speed setting won't close the gap).
- **Pronunciation.** Listen to every cue. A speech-to-text round-trip will not catch a mispronunciation, because it normalizes back to the intended word regardless of how it was actually said - only a human ear catches "gut" rendering as "gute," or two words blending into one at a boundary. Fix a phoneme-level issue with a phonetic respelling (forcing the intended vowel sound); fix a word-boundary blend by rephrasing, not respelling. Re-render and re-listen before locking either fix in.

### 5. Export the animation, then mux it with the narration

Export the animation as video from Claude Design (`Share → Export → Video`). Combine the exported video with the narration track into one file - the two are already time-aligned from step 3, so this is a straight audio/video mux (`ffmpeg -i video -i narration -c:v copy -c:a aac -shortest`), not a re-edit.

### 6. Publish

- **Video.** Upload the muxed file to YouTube via Claude in Chrome: title, description, tags, category, visibility, and subtitles built from the same cue timestamps as the narration script. Rich-text fields in YouTube Studio (title, description) are contenteditable `<div>`s, not real `<input>` elements, so drive them with click + select-all + type rather than a form-fill tool.
- **Interactive export, if you want one.** Export "Standalone HTML" from Claude Design's Share panel and host it with a real public URL (see "What you need" above) - a Claude Design link alone won't resolve for anyone outside the workspace. The export has no player API exposed (playback state lives inside its own runtime, and it rewrites its page body once after an internal unpack step), so don't try to control it programmatically. Instead, track whatever on-screen time readout the export already displays, and drive external audio's `currentTime` off that - re-attaching after the unpack step if needed, since anything appended to the page before that rewrite will be wiped along with it.

## Doing this for another skill

Repeat step 1 against that skill's own `SKILL.md` - the claims will be different, so the storyboard will be different - and the rest of the pipeline carries over unchanged.
