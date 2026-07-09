# `/huddle` explainer - narration script

Cue-by-cue script for [`narration.mp3`](./narration.mp3), timed to the video's exact 120.46-second timeline. Generated with ElevenLabs (`eleven_v3`, voice "Daniel - Steady Broadcaster", stability 0.45-0.5, style 0.15, similarity_boost 0.75) via the ElevenLabs MCP.

Two cues carry a deliberate phonetic respelling: "gutt" for "gut," which corrects a mispronunciation ("gute") that only surfaced on listening, not on a transcription round-trip. Don't "fix" it back to the dictionary spelling - it's intentional.

| # | Start | End | Line | Note |
|---|-------|-----|------|------|
| 0 | 0:00.0 | 0:06.2 | "We're losing users - and nobody can say why." | Reads the `question` prop; update if it changes |
| 1 | 0:06.2 | 0:09.6 | "An open-ended problem. No frame. No obvious answer." | |
| 2 | 0:10.8 | 0:16.2 | "Ask one voice, trained to agree - you get balance without conviction." | |
| 3 | 0:16.6 | 0:19.7 | "No gutt. No friction. No fight." | Phonetic respelling |
| 4 | 0:20.3 | 0:24.4 | "Real answers need a gutt, a critic, a fight." | Phonetic respelling - the thesis line |
| 5 | 0:24.8 | 0:33.4 | "Six lenses. Not six people - six ways of thinking, and everyone wears the same one at once." | New commentary; the legend act had none |
| - | 0:34.2 | 0:36.6 | *(on-screen text only, no narration)* | Window too tight (2.4s) for the 13-word on-screen line at a natural pace |
| 7 | 0:37.5 | 0:41.7 | "Blue Hat sizes the team first - this earns five." | Trimmed from the on-screen caption to match the voice's measured pace (~2.0-2.1 wps) and avoid overlapping cue 8 |
| 8 | 0:42.3 | 0:46.9 | "Then it seats the experts this problem needs." | Trimmed, same reason |
| 9 | 0:47.5 | 0:51.7 | "And sets the agenda - which hats, what order." | Trimmed, same reason |
| 10 | 0:52.7 | 0:59.8 | "White hat: everyone gathers facts - then challenges each other's, directly." | |
| 11 | 1:01.1 | 1:08.2 | "Red hat: gutt, licensed. The default model never gets to say this." | Phonetic respelling |
| 12 | 1:09.6 | 1:16.6 | "Black hat: attack mode - no voice sits out, but no straggler stalls the room either." | Deliberately shorter than the on-screen caption - matched to the voice's pace, not the text budget a reader can absorb |
| 13 | 1:17.9 | 1:25.0 | "Yellow hat: the same minds now argue the upside - and stress-test it." | |
| 14 | 1:26.3 | 1:33.4 | "Green hat: reframe it, invert it, break it on purpose." | |
| 15 | 1:34.7 | 1:40.3 | "Blue hat: five passes, braided into one." | |
| 16 | 1:42.2 | 1:47.9 | "It's a trust problem, not a pricing one - rebuild the first five minutes." | Reads the `answer` prop; update if it changes |
| 17 | 1:47.9 | 2:00.5 | "A smaller debate might've stopped at two. This one earned five experts, six hats, one answer - Huddle, built on Edward de Bono's Six Thinking Hats." | "Debate" is the real name of the size-2 tier in the sizing table, not a placeholder word |

## Notes for reuse

- Cues 7-9 originally ran longer than budgeted (14 words against a ~4.2s window, assuming ~3 words/sec) and cascaded into real audio overlaps of up to 1.6 seconds. The fix was shortening the text to the voice's actual measured pace, not raising playback speed - for the worst case, even the maximum speed setting wouldn't have closed the gap.
- Cue 17's first line went through two more candidates before landing: the original ("a simpler call") produced a word-boundary blend that sounded like "simplecall." A phonetic respelling wasn't the right tool here (the words are spelled correctly individually); a rephrase was.
- Assembly: each cue is delayed to its exact start time and mixed (not concatenated) into one track, so silence between cues comes from the gaps between delays rather than padded clips - this keeps the whole track sample-accurate against the video's own timeline.
