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
- **Leave the human markers alone.** The source records what is *more* common in human text than in AI text: plain "there is" / "it has" phrasing, plain verbs (*wrote, moved, used, tried, died* over *authored, relocated, utilized, attempted, passed away*), definite claims when true ("was the first," "is the only"), natural qualifiers (*very, perhaps, tends to*), and the odd wordy phrase ("in order to," "the fact that"). An edit pass that "tightens" these pushes prose toward the AI register. Don't strip them, and don't sprinkle them in either - they are byproducts of a person writing, not ingredients. A definite claim you can't verify stays as written and goes on the list of things for the author to confirm; softening it to "one of the first" is a guess in the other direction.

## The high-frequency tells

### 1. Puffery: inflated significance and promotional tone

AI inflates importance by asserting that the subject represents some broader trend or leaves a lasting mark - even for mundane subjects. The same reflex produces advertisement or travel-guide prose where a neutral writer would just describe the thing.

> Watch words (significance): *stands/serves as, is a testament/reminder, plays a vital/significant/crucial/pivotal/key role, underscores/highlights its importance, reflects broader, symbolizing its enduring/lasting, contributing to the, setting the stage for, marking a shift, key turning point, evolving landscape, focal point, indelible mark, deeply rooted, rich cultural heritage.*

> Watch words (promotion): *boasts a, vibrant, rich, profound, nestled, in the heart of, groundbreaking, renowned, diverse array, commitment to, natural beauty, exemplifies, showcasing.*

Current models are subtler than older ones: they avoid "the best" and stay faintly positive throughout. Look for a tone no neutral writer would hold, not only for superlatives.

**Fix:** Delete the significance claim, or replace it with the specific fact that would justify it. "The 1989 founding marked a pivotal moment in the evolution of regional statistics" → "It was founded in 1989." "Nestled in the heart of the region, the town boasts a vibrant market" → "The town has a weekly market." If there's a real reason it mattered, state that reason concretely.

### 2. Superficial analysis tacked onto sentence ends

A trailing present-participle ("-ing") phrase that editorializes about significance, impact, or implication - often a synthesis the sources don't support.

> Watch words: *highlighting/underscoring/emphasizing…, ensuring…, reflecting/symbolizing…, contributing to…, fostering…, cultivating…, encompassing…, enhancing…, valuable insights, aligning/resonating with…*

> "Douera enjoys close proximity to the capital, **further enhancing its significance as a dynamic hub of activity and culture.**"

**Fix:** Amputate the trailing clause. The factual half of the sentence usually stands fine alone.

### 3. Indirection: dodging "is," "has," and the plain relationship

Two habits with one cause - reaching past the simple verb. **Copula avoidance:** *serves as / stands as / marks / represents / functions as / operates as* where "is" would do; *boasts / features / offers / maintains* where "has" would do; in newer output, longer forms such as "ventured into politics as a candidate" for "was a candidate." **Vague connection:** *associated with, connected to, in connection with, in association with* where the writer should state what the relationship actually was.

> "Professor Jane Doe **was connected with** science education at Example University, which **serves as** a regional hub." → if the source says she taught there: "Professor Jane Doe taught science at Example University." The hub clause goes as puffery (tell 1). If no source says what she did there, the honest output is a question for the author, not a guessed verb.

This one bites hardest in copyedits: asked to "improve" plain text, a model swaps "is" for "serves as." In gate mode, don't.

"Serves as" and "stands as" are also puffery watch words. Cite tell 3 when the sentence is otherwise plain and tell 1 when the clause exists to inflate; when both apply, say so once.

**Fix:** Write "is," "has," or the verb that names the relationship (*founded, taught, owned, sued*). If you don't know the relationship, find out or cut the sentence - never supply a verb the text doesn't support.

### 4. The rule of three

Compulsive grouping in threes: tricolon adjectives ("significant, sustained, and verifiable"), three parallel clauses, three examples where two or four would be natural.

**Fix:** Break the pattern. Use one strong adjective, or a different count. Vary sentence rhythm so the triads don't drumbeat.

### 5. "Not X, but Y" / "Not only X, but also Y" / "Y rather than X"

A signature rhetorical frame used to manufacture profundity: the sentence corrects a misconception nobody held. The reversed form, "Y rather than X," is the same move and is especially common in Grok output.

> "This dispersal is **not** mere decoration **but** a deliberate becoming."

> "…**prioritizing empirical consolidation of power rather than ideological purity**."

**Fix:** State Y directly. Drop the contrived contrast unless the X is a real misconception worth correcting.

### 6. Canned emphasis on notability and sourcing

Hammering that a subject is notable by listing what kinds of outlets covered it, echoing sourcing-guideline language ("independent coverage," "national media outlets," "trade publications," "cited/featured/profiled in," "maintains an active social media presence"). The source ties this vocabulary to the newest model generation, so expect it in fresh output.

**Fix:** In normal prose, just state the fact and cite it once. Don't narrate the evidence about the evidence.

### 7. Filler vocabulary (high-density AI diction)

The overused words change with the model generation. The source dates them; each word appears once here, under the latest era that still overuses it (the checklist's B1 has the full per-era lists):

- **Mid-2025 on (GPT-5 era):** *emphasizing, enhance, highlighting, showcasing* - plus the notability phrases in tell 6.
- **Mid-2024 to mid-2025 (GPT-4o era):** *align with, bolstered, crucial, enduring, fostering, pivotal, underscore, vibrant.*
- **2023 to mid-2024 (GPT-4 era):** *Additionally* (opening a sentence), *boasts, delve, garner, interplay, intricate, key, landscape, meticulous, tapestry, testament, valuable.* Now rare in fresh output - *delve* dropped off sharply in 2025 - so a cluster of these dates a text more than it convicts it.
- **Undated in the source:** *robust, showcase, deep dive.* Grok has its own set: *causal, empirical, correlate,* and it still overuses *underscore.*
- **Not in the source's list, kept from wider reports:** *realm, navigate (the landscape), nuanced, multifaceted, leverage, seamless, holistic, comprehensive, resonate, stark, ever-evolving.*

Read the list literally: a word being overused does not condemn its synonyms, and context matters (an underscore can be a character). These words co-occur - where there is one, look for others, and count one alone for nothing. That goes for a sentence-opening *Additionally* too: transitions are only ever evidence inside a cluster.

**Fix:** Swap for plain words or cut. "Delve into" → "look at" / "examine" / cut. "A rich tapestry of" → just name the things. "Robust framework" → say what it actually does.

### 8. Formatting reflexes: Title Case, boldface, inline-header lists

AI capitalizes Every Main Word in section headings, scatters **bold** mid-sentence for emphasis, and breaks prose into lists where each bullet is a bold header, a colon, then a sentence of description. Related habits: a small table where a sentence would do; the document's own title repeated as a first heading; a heading that holds only sub-headings; "X and Y" headings such as "Awards and Recognition."

**Fix:** Use sentence case for headings unless the house style says otherwise. Reserve bold for genuine UI labels or defined terms. Turn header-colon bullets back into prose unless the content really is a list.

### 9. Em dashes and curly quotes

The signal is the formulaic em dash - spaced on both sides, punching up a clause or a parallelism the way sales copy does - not the raw count. The count is vendor-dependent and falling: GPT-5.1 suppresses em dashes, a July 2026 study found only Claude used them more than professional writers, and the source is considering retiring this sign. Treat it as supporting evidence inside a cluster, never alone. A spaced hyphen or en dash in the same role is not this tell: the source notes that people reach for those where models reach for the em dash. "Smart"/directional quotation marks where the surrounding document uses straight ones remain a copy-paste tell.

**Fix:** Where a dash is only adding drama, use a comma, a period, or parentheses. Follow the house style for dashes. Match the document's existing quote style.

### 10. Outline-like / promotional conclusions

A wrap-up paragraph that restates significance ("In conclusion, X stands as a testament…"), or a "Challenges and Future Directions" section grafted onto something that didn't need one.

**Fix:** End on the last real fact. Most factual writing needs no peroration.

### 11. Collaborative-chatbot leakage

Text addressed to a user rather than a reader: "I hope this helps!", "Certainly! Here's…", "Would you like me to…", "As an AI…", "Let me know if you'd like me to expand." Also knowledge-cutoff disclaimers ("As of my last update…") and self-references.

**Fix:** Strip every trace of the chat frame. The deliverable is the prose, not a message about the prose.

### 12. Markup leaking from the chat window

Markdown appearing in a target that doesn't render it (wikitext, plain email, a CMS field): `**bold**`, `## headers`, `* bullets`, a `#` heading on every section, a `---` rule between every section. None of these is a tell in a document that is meant to be Markdown - leave correct markup alone. Worse, and unambiguous: the chatbot's internal citation markers pasted along with the text - ChatGPT `contentReference`, `oaicite`, `turn0search0`; Gemini `[cite: 1]`; Grok `grok_card`; DeepSeek `【85†L261-269】`; Perplexity `[attached_file:1]` and `[web:1]`.

**Fix:** Convert to the target format's actual markup, or remove. Delete vendor markers outright, then check the claim they were attached to - the marker stood where a source should be.

### 13. Fabricated or broken citations

AI invents plausible-looking sources, dead URLs, fake DOIs, or attributes claims to named people/outlets that never said them ("Roger Ebert highlighted the lasting influence…"). Source URLs may also carry the chatbot's tracking parameter (`utm_source=chatgpt.com`, `utm_source=copilot.com`, `referrer=grok.com`) - proof a chatbot found the link, though not that it wrote the prose.

**Fix:** Verify every citation actually exists and supports the claim. Never let an unverifiable reference through. If you can't confirm a source, remove the claim or flag it explicitly. Strip tracking parameters from URLs.

### 14. Gratuitous cross-references

Naming a sibling skill, command, or concept as analogy or aside when the reader doesn't need to understand it to follow the instructions. The reference adds comprehension cost ("what's `marathon` - do I need to read that first?") with no behavioural payoff; the sentence would instruct identically without it.

> "This dispersal works **exactly as `marathon` composes `pr-review-merge`.**"

Distinct from a load-bearing composition pointer the reader must actually follow ("composes `skill-forge`'s A/B equivalence capability") - that one is legitimate, don't flag it.

**Fix:** Cut the analogy. If the reader genuinely needs the referenced skill, make it a declared dependency, not a passing mention. This is prose-level judgment only - it catches decorative name-drops, not whether a document's real composition graph is correct.

## Output formats

**When editing:** Return the cleaned text. If the user wants to see what changed, follow with a short bullet list of the categories you hit and why - quote the worst offenders.

**When auditing without editing:** Produce a findings list. For each issue: the quoted phrase, the category number above (or the checklist id, such as B6, when the tell appears only in `references/full-checklist.md`), and a one-line fix. Some words sit under more than one tell (*boasts, vibrant, showcasing, enhancing, serves as*): cite the most specific tell for what the sentence is doing, once. Close with an overall verdict (e.g., "heavy slop - puffery and rule-of-three throughout" vs. "mostly clean, two trailing-participle clauses").

**Always:** Prioritize the underlying emptiness over surface tics. If removing the slop would gut the text down to nothing, that's the real finding - say so. The fix for a paragraph that only asserts importance is to get a real fact or delete it, not to reword the puffery.

For the complete pattern catalog (including vague attributions, heading-structure tells, small tables, change-description narration, emoji-as-formatting, letter-like writing, date-handling tells, the full vendor-marker list, the human markers to leave alone, and the historical tells that date older text - elegant variation, section summaries, prompt-refusal artifacts), see `references/full-checklist.md`.

## Provenance and freshness

Derived from Wikipedia's "Signs of AI writing" (`Wikipedia:Signs_of_AI_writing`) at **revision 1374941330** (dated 14 September 2026, captured 19 September 2026): `https://en.wikipedia.org/w/index.php?title=Wikipedia:Signs_of_AI_writing&oldid=1374941330`. The previous capture was 29 May 2026; the page took about 400 revisions in between.

The tells drift as models change - diction that marked one model generation reads clean in the next, and new tics appear. Treat this snapshot as a point-in-time field guide, not a permanent one. **If this skill has not been updated in a while, strongly prefer re-deriving it:** fetch the pinned revision (the URL above) and the live page (`https://en.wikipedia.org/w/index.php?title=Wikipedia:Signs_of_AI_writing`), appending `&action=raw` to either for wikitext, diff the section headings and the "Words to watch" boxes first - most other churn is worked examples - then refresh the patterns and move the pin. A stale slop-detector is worse than none, because it gives false confidence while missing the current generation's tells.

**Adapted, not copied.** The source is a detection guide for Wikipedia and calls itself "descriptive, not prescriptive"; the fixes here are this skill's. Three entries are this skill's own or generalised from a Wikipedia-specific section: gratuitous cross-references (tell 14), date-handling tells, and change-description narration (generalised from the source's "Edit summaries").

**Deliberately excluded** as Wikipedia process rather than prose: draft submission statements, canned user pages, permissions gaming, pre-placed maintenance templates, non-existent categories and templates, broken wikitext, comment indicators about invented policy shortcuts and transcluded banners, and "Biases in content" (not detectable from the prose alone).
