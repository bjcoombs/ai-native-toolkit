# Full checklist: Signs of AI writing

Exhaustive companion to SKILL.md, adapted from Wikipedia's "Signs of AI writing" at **revision 1374941330** (dated 14 September 2026, captured 19 September 2026). Use when doing a thorough audit. The summary in SKILL.md covers the most common offenders; this file adds the rest plus extra detail. No single sign is conclusive - look for clusters.

> **Freshness:** these tells track current models and will drift. If this checklist has not been refreshed in a while, re-derive it from the live Wikipedia page before trusting an audit - see the "Provenance and freshness" note in SKILL.md, which also lists what was adapted and what was deliberately left out.

> **Historical entries.** Entries marked *(historical)* were common in older models and are rare in current output. They help date a text; on fresh output they are weak evidence, so don't let one carry a verdict.

## Table of contents
- A. Content tells
- B. Language and grammar tells
- C. Style tells
- D. Communication / chatbot-leakage tells
- E. Markup tells
- F. Citation tells
- G. Human markers - leave these alone

---

## A. Content tells

**A1. Puffery - significance, legacy, broader trends.** Inflates importance, claims the subject represents a wider movement, leaves an "indelible mark," etc. Applied even to etymology or population figures. Sometimes prefaced with a fake-humble hedge ("though little-known, X nonetheless…") before puffing anyway. More characteristic of ChatGPT and Grok than of Gemini or Claude. *Watch:* stands/serves as, testament/reminder, crucial/pivotal/vital/significant/key role or moment, underscores/highlights its importance, reflects broader, enduring legacy, setting the stage for, marks a shift, evolving landscape, focal point, deeply rooted, rich cultural heritage. **Fix:** cut, or substitute a concrete fact.

**A2. Biology/ecology over-emphasis.** For species, over-stresses ecosystem connections and conservation status even when tenuous or unknown ("the general health of the ecosystem is crucial for this species"). **Fix:** keep only documented facts.

**A3. Canned notability / source-narration.** Lists *types* of outlets, echoes sourcing-guideline jargon ("independent coverage," "national/regional media," "trade publications," "cited/featured/profiled in," "leading expert"), narrates the evidence rather than stating facts. The source ties this vocabulary to the mid-2025-on model generation. **Fix:** state the fact, cite once.

**A4. "Active social media presence."** Idiosyncratic AI phrasing: "maintains a strong digital presence," "consistently demonstrated excellence in digital promotions." **Fix:** delete or replace with a specific, sourced detail.

**A5. Superficial analysis via trailing "-ing" clause.** Editorializing tacked on the end: "…, highlighting its role as a regional hub," "…, contributing to the socio-economic development of the region," "…, enhancing its significance." Often unsupported synthesis. **Fix:** amputate the clause.

**A6. Vague attributions / overgeneralized opinion.** "Industry experts say," "many critics argue," "it is widely regarded," with no actual source - or, in RAG models, a named source that didn't say it. *Watch:* some critics, observers note, it is considered, widely regarded as. **Fix:** attribute to a real source or remove.

**A7. Rule of three.** Tricolons everywhere: "significant, sustained, and verifiable"; three parallel clauses; three examples by default. **Fix:** vary the count and rhythm.

**A8. Outline-like / promotional conclusions.** "Challenges," "Future Directions," "As the global landscape evolves…" sections grafted on; closing paragraphs that restate grand significance. **Fix:** end on the last real fact.

**A9. Section summaries** *(historical)*. *Watch:* In summary, In conclusion, Overall… closing a section that didn't need summarizing. Rare in current models. **Fix:** delete.

**A10. Leads that read like a definition/essay prompt.** Opening that treats the topic as an abstract concept to be explored rather than a subject to be described; a descriptive title introduced as if it were a proper noun, often with "refers to." **Fix:** lead with who/what/when/where.

**A11. Date-handling tells.** Vague or self-dating temporal references that will rot or that hide a missing fact: "in recent years," "currently," "to date," "as of this writing," "the latest," present-tense claims about things that change, round-number estimates with false precision. Also inconsistent or silently US-default date formats (Month DD, YYYY) where the document otherwise uses DMY/ISO. Distinct from D3 (chatbot knowledge-cutoff disclaimers): this is the prose itself failing to anchor a time-bound fact. **Fix:** anchor to a specific, sourced, dated fact ("as of the 2021 census"), or remove the claim; match the document's established date format.

**A12. Gratuitous cross-references.** Names a sibling skill, command, or concept as analogy or aside when the reader doesn't need to understand that reference to follow the document's instructions. The reference adds comprehension cost ("what's `marathon` - do I need to read that first?") without contributing behaviour; the sentence would instruct identically without it. Distinct from a load-bearing composition pointer ("composes `skill-forge`'s A/B equivalence capability"), which the reader MUST follow to use the document - that one is legitimate, don't flag it. *Watch:* "exactly as X does Y", "similar to how Z works", "like the X skill", "in the style of Y", "mirrors the approach in Z" - when illustrative rather than instructional. **Fix:** cut the analogy; if the reference is load-bearing, make it explicit via the document's declared dependencies. *Advisory and prose-level only: it flags apparent decorative references from textual context and can't see the document's real dependency graph, so it surfaces candidates for human judgment, not violations.*

Decorative (flag):
- "This dispersal works exactly as `marathon` composes `pr-review-merge`" - the dispersal is understandable without knowing `marathon`.
- "Similar to how the `huddle` skill gathers perspectives" - an illustrative aside, not a required pre-read.
- "In the style of `assess`'s tiered severity model" - the severity model is described inline; the name adds nothing.

Load-bearing (do NOT flag):
- "Composes `skill-forge`'s A/B equivalence capability for behavioural validation" - the reader must understand `skill-forge` to use this.
- "Inherits the transfer-set format from `semantic-compress`'s distill mode" - a functional dependency.
- "See `assess`'s severity-tier table for rating guidance" - an explicit pointer to a required reference.

**A13. Promotional and advertisement-like language.** Travel-guide or brochure tone where a neutral writer would describe. Appears even when nobody is trying to advertise, and when a rewrite claims to have "removed promotional tone." Older models are blatantly positive; current ones avoid "the best" and stay subtly positive throughout, so judge the tone across the passage rather than hunting superlatives. *Watch:* boasts a, vibrant, rich, profound, nestled, in the heart of, groundbreaking, renowned, diverse array, commitment to, natural beauty, exemplifies, enhancing, showcasing, featuring. **Fix:** describe what is there ("The town has a weekly market"); cut adjectives no source would support.

**A14. Vague expression of connection or association.** Alludes to a link instead of stating it: "sources identified John Doe as being associated with leadership of ExampleCorp" for "John Doe was the CEO of ExampleCorp"; "has been associated with residential water management applications"; "referenced the inventor in connection with environmental award recognition." Often stacked with promotional words ("widely associated"). Indirection alone is weak evidence - the tell is a relationship the writer should know, left unstated. *Watch:* in connection with/to, connected with/to, in association with, associated with. **Fix:** name the relationship with its verb (was CEO of, taught at, won, sued) when the text or its source supports one. If it can't be established, cut the sentence or return it as a question for the author; never supply a guessed verb.

---

## B. Language and grammar tells

**B1. High-density AI diction.** The overused words change with the model generation; the source dates them:
- *Mid-2025 on (GPT-5 era):* emphasizing, enhance, highlighting, showcasing - plus the notability phrases in A3.
- *Mid-2024 to mid-2025 (GPT-4o era):* align with, bolstered, crucial, emphasizing, enhance, enduring, fostering, highlighting, pivotal, showcasing, underscore, vibrant.
- *2023 to mid-2024 (GPT-4 era):* Additionally (opening a sentence), boasts, bolstered, crucial, delve, emphasizing, enduring, garner, intricate/intricacies, interplay, key, landscape, meticulous, pivotal, underscore, tapestry, testament, valuable, vibrant. *Delve* dropped off sharply in 2025; a cluster from this era dates a text more than it convicts it.
- *Undated in the source:* robust, showcase, deep dive. Grok is idiosyncratic: causal, empirical, correlate, and it still overuses underscore.
- *Not in the source's list, kept from wider reports:* realm, navigate, nuanced, multifaceted, leverage, seamless, holistic, comprehensive, resonate, stark, ever-evolving, treasure trove, game-changer, in the realm of, when it comes to, it's worth noting, it's important to note.

Read the list literally: overuse of a word does not extend to its synonyms, and context matters (an underscore can be a character; a landscape can be a view). The words co-occur - one suggests looking for others. **Fix:** plain synonyms or cut.

**B2. "Not X, but Y" / "Not only X, but also Y" / "Y rather than X."** Manufactured contrast for false depth: the sentence corrects a misconception nobody held. The reversed form ("prioritizing empirical consolidation of power rather than ideological purity") is the same move and is especially common in Grok output. **Fix:** state Y plainly; keep the contrast only when X is a real misconception.

**B3. Elegant variation / forced lexical diversity** *(historical)*. A repetition penalty in older models made them rename the same thing to avoid reuse ("the artist… the painter… the creator… the visionary"), producing unnatural synonym-cycling. The source moved this to its historical section in 2026. **Fix:** use the plain repeated noun; repetition is fine.

**B4. Excessive hedging / both-sides padding.** "While some may argue… others contend…" balancing where no real controversy exists. This is structural padding, not word-level qualification: a natural "perhaps" or "tends to" is a human marker (see G) and stays. **Fix:** cut to the substantive point.

**B5. Em dashes.** The signal is the formulaic em dash: spaced on both sides, punching up a clause or a parallelism in the manner of sales copy, in places a person would use a comma, parentheses or a colon. Raw frequency is vendor-dependent and falling - GPT-5.1 suppresses em dashes, and a July 2026 study found only Claude used them more than professional writers while ChatGPT used them less. The source is considering retiring the sign. Supporting evidence inside a cluster, never a tell alone. A spaced hyphen or en dash in the same role is not this tell: the source notes that people use those where models use the em dash. **Fix:** where the dash only adds drama, use a comma, period or parentheses; follow the house style for dashes.

**B6. Transitional adverbs - only in a cluster.** Additionally, Moreover, Furthermore, Consequently, Notably, Importantly opening many sentences. The source lists transition words in isolation as an *ineffective* indicator: only a few are overused, essay-writing humans do the same, and style guides accept it. Never flag on this alone - and that includes *Additionally*, which B1 lists: it counts only alongside other B1 words. **Fix:** inside an otherwise sloppy passage, thin them out and let sentences connect by content.

**B7. Avoidance of "is" and "has."** Replaces the copula with a grander verb: *serves as / stands as / marks / represents / functions as / operates as* for "is" (the first two are also A1 watch words: cite B7 when the sentence is otherwise plain, A1 when the clause exists to inflate); *boasts / features / offers / maintains* for "has" ("has been featured" is a different construction and fine); *refers to* in a lead sentence, as if the piece were about the term. Newer output uses longer forms: "ventured into politics as a candidate" for "was a candidate," "holds the distinction of being" for "is." Observed in GPT and Gemini models, and most visible in AI copyedits, which "improve" plain sentences this way. **Fix:** restore "is," "are," "has," "was."

---

## C. Style tells

**C1. Title Case headings.** Capitalizing Every Main Word. **Fix:** sentence case unless house style dictates.

**C2. Overuse of boldface.** Bold scattered mid-sentence for emphasis. **Fix:** reserve for true labels/defined terms.

**C3. Curly/directional quotation marks** where the document otherwise uses straight quotes - a paste tell. **Fix:** match surrounding style.

**C4. Emoji as formatting.** Emoji prefixing headings or bullets (✅, 🚀, 📌). **Fix:** remove in formal/encyclopedic/professional contexts.

**C5. Section titles in plain text.** Output broken into pseudo-sections with bare title lines ("Importance of Thorough Research") that aren't real headings. Common in long AI-written comments and messages. **Fix:** integrate into prose or use real headings.

**C6. Inline-header vertical lists.** A list where each item is a marker, a bold header, a colon, then a sentence of description. When pasted as bare text the marker may survive as a bullet character, hyphen, en dash, explicit `1.` numbering or an emoji. **Fix:** write it as prose unless the content really is a list of parallel items; then drop the bold headers.

**C7. Small tables in place of prose.** A minimally formatted table of a few cells that a sentence would carry better. **Fix:** fold it into prose; keep tables for data a reader will scan or compare.

**C8. Heading-structure tells.**
- The document's own title repeated as a heading above the content, where the platform already supplies the title.
- A heading that holds only sub-headings and no text of its own.
- Skipped levels: sections that start at level 3 with no level 2 above them.
- "X and Y" headings, above all "Awards and recognition" (or plain "Recognition") - the puffery of A1 and A3 in heading form.

**Fix:** delete the duplicate title; give each heading text or remove it; keep levels contiguous; name the section for what it contains.

---

## D. Communication / chatbot-leakage tells

**D1. Collaborative communication aimed at a user.** "Certainly!", "I hope this helps!", "Here's a draft…", "Would you like me to expand?", "Let me know if…". **Fix:** strip entirely.

**D2. Self-reference / "as an AI."** Any mention of being a model, assistant, or language model. **Fix:** delete.

**D3. Knowledge-cutoff disclaimers & speculation about source gaps.** "As of my last update," "up to my last training update," "while specific details are limited/scarce," "not widely documented," "in the provided sources/search results," "based on available information." **Fix:** delete; if a fact is genuinely uncertain, verify it instead of hedging.

**D4. Prompt-refusal artifacts** *(historical)*. Leftover safety/refusal language ("I cannot assist with that," "as an AI I'm unable to…") embedded in a deliverable. Rare now, unambiguous when found. **Fix:** remove.

**D5. Letter-like writing in the wrong place.** Salutations and valedictions ("Dear editors," "I hope this message finds you well," "Best regards") on content that isn't a letter (e.g., a wiki talk message or a doc). **Fix:** drop the epistolary frame.

**D6. Change-description narration.** Generalised from the source's section on edit summaries to commit messages, PR descriptions, changelogs and review replies. The description reassures instead of reporting:
- *Narrated compliance:* "ensured the content adheres to guidelines," "in compliance with the style guide," "maintains a neutral tone."
- *Procedural reassurance:* "preserved," "retained," "avoided," "ensured," "aimed to" - listing what was *not* broken.
- *Unspecific improvement:* "refined," "enhanced," "enriched," "streamlined," "improved clarity and flow," "added sourced content," "improved attribution."
- *Markup tour:* itemising field names and formatting details nobody asked about.
- Length out of proportion to the change.

**Fix:** say what changed and why, in the terms a reviewer would check: "Replace the em-dash entry with a spaced-dash rule; the source now ties frequency to one vendor." One specific sentence beats a paragraph of assurance.

---

## E. Markup tells

**E1. Markdown bleeding into a non-Markdown target.** `**bold**`, `## headings`, `- bullets`, and bracket-then-parenthesis links pasted into wikitext, plain-text email, or a CMS that doesn't render it. Two structural forms of the same leak: a top-level `#` heading on every section, and a `---` thematic break between every section. All of E1 applies only where the target does not render Markdown; in a Markdown document this is correct markup and stays. **Fix:** convert to the target's real markup or remove.

**E2. Broken / placeholder markup.** Malformed links, leftover `[[ ]]` or `{{ }}` fragments, `[insert citation]`, `[Source]`, `[Year]` placeholders. **Fix:** complete or remove.

**E3. Vendor citation markers.** The chatbot's internal reference format pasted along with the text. Unambiguous: no human types these.
- *ChatGPT:* `contentReference`, `oaicite`, `oai_citation`, `turn0search0`, `attributableIndex`, a stray `+1`.
- *Gemini:* `[cite: 1]`, `[cite: 3, 12, 13]`, and `[span_1]` followed by `(start_span)`.
- *Grok:* `grok_card` tags, `grok_render_citation_card_json`.
- *DeepSeek and derivatives:* lenticular brackets with a dagger, such as `【85†L261-269】`.
- *Perplexity:* `[attached_file:1]`, `[web:1]`; source URLs containing `ppl-ai-file-upload`.
- *Unclassified (first seen June 2026):* `:::writing{variant="document" id="12345"}`, often with a closing `:::`.

**Fix:** delete the marker, then check the claim it was attached to - the marker stood where a source should be.

---

## F. Citation tells

**F1. Fabricated sources.** Plausible-looking but nonexistent books, articles, DOIs, ISBNs, or URLs; a DOI that resolves to an unrelated article. **Fix:** verify each; remove any you can't confirm.

**F2. Broken external links.** Multiple 404s / dead domains in a new piece - strong AI signal. **Fix:** verify links resolve and support the claim.

**F3. Misattributed claims.** A real source named, but it doesn't actually say what's attributed to it (common with RAG models: "Roger Ebert highlighted the lasting influence…"). **Fix:** check the source actually supports the sentence.

**F4. Over-citation of trivia.** Inline-citing uncontroversial or trivial facts a human would leave unsourced, often echoing guideline wording. **Fix:** normalize to sensible citation density.

**F5. Tracking parameters in source URLs.** `utm_source=openai`, `utm_source=chatgpt.com`, `utm_source=copilot.com`, `referrer=grok.com`. Near-proof that a chatbot found the link; not proof that it wrote the prose, since people use chatbots to find sources for their own text. Gemini and Claude add these less often. **Fix:** strip the parameter, and check the link supports the sentence.

---

## G. Human markers - leave these alone

The source records constructions that are *more* common in human-written text than in AI text. An audit must not flag them and an edit must not "tighten" them away:

- Simple is/has phrases: "there is a," "it has a."
- Plain verbs over stiff or euphemistic ones: *wrote* (not authored), *moved* (not relocated), *used* (not utilized), *tried* (not attempted), *died* (not passed away).
- Superlative or definite statements: "one of the best," "is the only," "was the first." A definite factual claim ("is the only," "was the first") that can't be verified stays as written and goes on the list for the author to confirm; don't soften it. An evaluative one with no source behind it ("one of the best restaurants in the region") is A1/A13 puffery and is handled there - the source's point is that humans make such statements more freely, not that they are exempt.
- Qualifiers and intensifiers: *very, perhaps, tends to.*
- Isolated wordy constructions: "as a result of," "in order to," "all of the," "the fact that."

Don't insert them either. They are byproducts of a person writing, not ingredients; sprinkled in, they become the next tell.

The source also lists indicators that do **not** work, so don't reason from them: perfect grammar; "bland" or "robotic" prose; formal or academic register in general (the overuse is of *specific words*); a mix of casual and formal registers; transition words in isolation (B6); missing citations.

---

## Verdict guidance

Weigh clusters, not isolated hits. Two questions are in play and they are separate:

- **Quality - how much of the text survives the fixes?** This sets the bin below, and the age of the tells is irrelevant to it: empty prose is slop whatever produced it.
- **Provenance - does it read as AI-written, and from when?** Here *(historical)* entries and dated vocabulary count as weak evidence on recent text and as dating evidence on older text.

Bins, by what is left once the tells are fixed:
- **Clean:** at most a couple of incidental tics; real specificity throughout.
- **Light slop:** the facts are there and the tells sit on top of them; fixable in place.
- **Heavy slop:** fixing the tells leaves little or nothing - the prose is fluent but largely contentless. The honest fix is to rebuild around real facts, not to reword. For a single sentence, say what survives rather than forcing a bin.

Stay humble about the verdict. The source reports that untrained readers tell AI text from human text at about chance, and that even heavy LLM users mislabel roughly one text in ten. People's writing is also drifting toward LLM habits. Say "reads as" rather than "is."

Always remember: the surface phrases are symptoms. The disease is content that says nothing while sounding important. Treat the disease.
