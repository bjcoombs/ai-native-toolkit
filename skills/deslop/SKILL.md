---
name: deslop
description: "Detect and remove the telltale signs of AI-generated 'slop' from any written text - articles, reports, emails, essays, bios, marketing copy, documentation, encyclopedia entries, or anything meant to read as if a thoughtful human wrote it. Apply silently as a quality gate before finalizing substantial prose, and explicitly when asked to clean a draft. TRIGGER when the user says 'make this sound less like AI', 'remove the AI tells', 'de-slop this', 'check if this reads as AI-written', 'make it sound human', 'edit out the ChatGPT voice', or critiques a draft as generic, puffy, or robotic. Based on Wikipedia's 'Signs of AI writing' field guide."
---

# Deslop: removing the signs of AI writing

LLMs have an identifiable writing style. Left unchecked, AI prose regresses toward the statistical mean: it smooths specific, unusual, verifiable facts into generic, positive, important-sounding filler. The result reads fluent but hollow - "slop." This skill is a field guide to catching and fixing those tells.

## How to use this skill

There are two modes:

1. **Gate mode (default, silent).** When you are *writing* substantial prose, self-check the draft against the patterns below before presenting it. Don't announce that you're doing this; just produce clean output.
2. **Audit mode (explicit).** When the user gives you text and asks you to de-slop it, critique it, or check whether it sounds AI-written, scan against every category, then either (a) return an edited version, or (b) return a findings list with specific quoted offenders and fixes - match whatever the user asked for.

For a full audit of an external file, read `references/full-checklist.md` for the exhaustive pattern list with examples. The summary below covers the high-frequency offenders that catch ~90% of slop.

## Critical mindset

- **The patterns are signals, not crimes.** Humans write some of these too (blogs, editorials, press releases). The presence of one phrase doesn't condemn a text; a *cluster* of them is the tell. Don't mechanically purge every "however."
- **Fixing the surface tic is not the goal - fixing the underlying emptiness is.** Deleting the word "underscores" while leaving a sentence that says nothing just makes the slop harder to detect. If a sentence only puffs up significance and carries no fact, cut the whole sentence, don't reword it.
- **Specificity is the antidote.** The core failure of slop is vagueness masquerading as importance. Replace "a revolutionary titan of industry" with "inventor of the first train-coupling device." When you can't add a real fact, delete the claim.

## The high-frequency tells

### 1. Puffery: undue emphasis on significance and legacy

AI inflates importance by asserting that the subject represents some broader trend or leaves a lasting mark - even for mundane subjects.

> Watch words: *stands/serves as, is a testament/reminder, plays a vital/significant/crucial/pivotal/key role, underscores/highlights its importance, reflects broader, symbolizing its enduring/lasting, contributing to the, setting the stage for, marking a shift, key turning point, evolving landscape, focal point, indelible mark, deeply rooted, rich cultural heritage.*

**Fix:** Delete the significance claim, or replace it with the specific fact that would justify it. "The 1989 founding marked a pivotal moment in the evolution of regional statistics" → "It was founded in 1989." If there's a real reason it mattered, state that reason concretely.

### 2. Superficial analysis tacked onto sentence ends

A trailing present-participle ("-ing") phrase that editorializes about significance, impact, or implication - often a synthesis the sources don't support.

> Watch words: *highlighting/underscoring/emphasizing…, ensuring…, reflecting/symbolizing…, contributing to…, fostering…, cultivating…, encompassing…, valuable insights, aligning/resonating with…*

> "Douera enjoys close proximity to the capital, **further enhancing its significance as a dynamic hub of activity and culture.**"

**Fix:** Amputate the trailing clause. The factual half of the sentence usually stands fine alone.

### 3. The rule of three

Compulsive grouping in threes: tricolon adjectives ("significant, sustained, and verifiable"), three parallel clauses, three examples where two or four would be natural.

**Fix:** Break the pattern. Use one strong adjective, or a different count. Vary sentence rhythm so the triads don't drumbeat.

### 4. "Not X, but Y" / "Not only X, but also Y"

A signature rhetorical frame used to manufacture profundity.

> "This dispersal is **not** mere decoration **but** a deliberate becoming."

**Fix:** State Y directly. Drop the contrived contrast unless the X is a real misconception worth correcting.

### 5. Canned emphasis on notability and sourcing

Hammering that a subject is notable by listing what kinds of outlets covered it, echoing sourcing-guideline language ("independent coverage," "national media outlets," "profiled in," "maintains an active social media presence").

**Fix:** In normal prose, just state the fact and cite it once. Don't narrate the evidence about the evidence.

### 6. Filler vocabulary (high-density AI diction)

Overused across LLM output: *delve, tapestry, testament, realm, navigate (the landscape), boasts, robust, nuanced, multifaceted, intricate, pivotal, crucial, vital, foster, underscore, garner, showcase, leverage, seamless, holistic, comprehensive, rich (history/heritage), align with, resonate, vibrant, stark, meticulous, ever-evolving.*

**Fix:** Swap for plain words or cut. "Delve into" → "look at" / "examine" / cut. "A rich tapestry of" → just name the things. "Robust framework" → say what it actually does.

### 7. Title Case in headings + overuse of boldface

AI capitalizes Every Main Word in section headings and scatters **bold** mid-sentence for emphasis.

**Fix:** Use sentence case for headings unless the house style says otherwise. Reserve bold for genuine UI labels or defined terms, not for emphasis on ordinary phrases.

### 8. Em-dash overuse and curly quotes

Heavy reliance on em dashes for dramatic asides, and "smart"/directional quotation marks where the surrounding document uses straight ones (a copy-paste tell).

**Fix:** Vary punctuation - commas, periods, parentheses. Match the document's existing quote style.

### 9. Outline-like / promotional conclusions

A wrap-up paragraph that restates significance ("In conclusion, X stands as a testament…"), or a "Challenges and Future Directions" section grafted onto something that didn't need one.

**Fix:** End on the last real fact. Most factual writing needs no peroration.

### 10. Collaborative-chatbot leakage

Text addressed to a user rather than a reader: "I hope this helps!", "Certainly! Here's…", "Would you like me to…", "As an AI…", "Let me know if you'd like me to expand." Also knowledge-cutoff disclaimers ("As of my last update…") and self-references.

**Fix:** Strip every trace of the chat frame. The deliverable is the prose, not a message about the prose.

### 11. Markdown bleeding into the wrong format

`**bold**`, `## headers`, or `* bullets` appearing in a context that doesn't use Markdown (wikitext, plain email, a CMS field). A dead giveaway of pasted AI output.

**Fix:** Convert to the target format's actual markup, or remove.

### 12. Fabricated or broken citations

AI invents plausible-looking sources, dead URLs, fake DOIs, or attributes claims to named people/outlets that never said them ("Roger Ebert highlighted the lasting influence…").

**Fix:** Verify every citation actually exists and supports the claim. Never let an unverifiable reference through. If you can't confirm a source, remove the claim or flag it explicitly.

### 13. Gratuitous cross-references

Naming a sibling skill, command, or concept as analogy or aside when the reader doesn't need to understand it to follow the instructions. The reference adds comprehension cost ("what's `marathon` - do I need to read that first?") with no behavioural payoff; the sentence would instruct identically without it.

> "This dispersal works **exactly as `marathon` composes `pr-review-merge`.**"

Distinct from a load-bearing composition pointer the reader must actually follow ("composes `skill-forge`'s A/B equivalence capability") - that one is legitimate, don't flag it.

**Fix:** Cut the analogy. If the reader genuinely needs the referenced skill, make it a declared dependency, not a passing mention. This is prose-level judgment only - it catches decorative name-drops, not whether a document's real composition graph is correct.

## Output formats

**When editing:** Return the cleaned text. If the user wants to see what changed, follow with a short bullet list of the categories you hit and why - quote the worst offenders.

**When auditing without editing:** Produce a findings list. For each issue: the quoted phrase, the category number above, and a one-line fix. Close with an overall verdict (e.g., "heavy slop — puffery and rule-of-three throughout" vs. "mostly clean, two trailing-participle clauses").

**Always:** Prioritize the underlying emptiness over surface tics. If removing the slop would gut the text down to nothing, that's the real finding - say so. The fix for a paragraph that only asserts importance is to get a real fact or delete it, not to reword the puffery.

For the complete pattern catalog (including vague attributions, "elegant variation," letter-like talk-page writing, emoji-as-formatting, section-title-in-plaintext, prompt-refusal artifacts, and date-handling tells), see `references/full-checklist.md`.

## Provenance and freshness

Derived from Wikipedia's "Signs of AI writing" (`Wikipedia:Signs_of_AI_writing`) as captured on **29 May 2026**. The tells drift as models change - diction that marked one model generation reads clean in the next, and new tics appear. Treat this snapshot as a point-in-time field guide, not a permanent one. **If this skill has not been updated in a while, strongly prefer re-deriving it: pull the live Wikipedia page, diff it against this version, and refresh the patterns before relying on the output.** A stale slop-detector is worse than none, because it gives false confidence while missing the current generation's tells.
