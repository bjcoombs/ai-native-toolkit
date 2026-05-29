# Full checklist: Signs of AI writing

Exhaustive companion to SKILL.md, adapted from Wikipedia's "Signs of AI writing." Use when doing a thorough audit. The summary in SKILL.md covers the most common offenders; this file adds the rest plus extra detail. No single sign is conclusive — look for clusters.

## Table of contents
- A. Content tells
- B. Language and grammar tells
- C. Style tells
- D. Communication / chatbot-leakage tells
- E. Markup tells
- F. Citation tells

---

## A. Content tells

**A1. Puffery — significance, legacy, broader trends.** Inflates importance, claims the subject represents a wider movement, leaves an "indelible mark," etc. Applied even to etymology or population figures. Sometimes prefaced with a fake-humble hedge ("though little-known, X nonetheless…") before puffing anyway. *Watch:* stands/serves as, testament, pivotal/crucial/vital/key role, underscores its importance, reflects broader, enduring legacy, evolving landscape, deeply rooted, rich cultural heritage. **Fix:** cut, or substitute a concrete fact.

**A2. Biology/ecology over-emphasis.** For species, over-stresses ecosystem connections and conservation status even when tenuous or unknown ("the general health of the ecosystem is crucial for this species"). **Fix:** keep only documented facts.

**A3. Canned notability / source-narration.** Lists *types* of outlets, echoes sourcing-guideline jargon ("independent coverage," "national/regional media," "profiled in," "leading expert"), narrates the evidence rather than stating facts. **Fix:** state the fact, cite once.

**A4. "Active social media presence."** Idiosyncratic AI phrasing: "maintains a strong digital presence," "consistently demonstrated excellence in digital promotions." **Fix:** delete or replace with a specific, sourced detail.

**A5. Superficial analysis via trailing "-ing" clause.** Editorializing tacked on the end: "…, highlighting its role as a regional hub," "…, contributing to the socio-economic development of the region." Often unsupported synthesis. **Fix:** amputate the clause.

**A6. Vague attributions / overgeneralized opinion.** "Industry experts say," "many critics argue," "it is widely regarded," with no actual source — or, in RAG models, a named source that didn't say it. *Watch:* some critics, observers note, it is considered, widely regarded as. **Fix:** attribute to a real source or remove.

**A7. Rule of three.** Tricolons everywhere: "significant, sustained, and verifiable"; three parallel clauses; three examples by default. **Fix:** vary the count and rhythm.

**A8. Outline-like / promotional conclusions.** "Challenges," "Future Directions," "As the global landscape evolves…" sections grafted on; closing paragraphs that restate grand significance. **Fix:** end on the last real fact.

**A9. Section summaries.** *Watch:* In summary, In conclusion, Overall… closing a section that didn't need summarizing. **Fix:** delete.

**A10. Leads that read like a definition/essay prompt.** Opening that treats the topic as an abstract concept to be explored rather than a subject to be described. **Fix:** lead with who/what/when/where.

---

## B. Language and grammar tells

**B1. High-density AI diction.** delve, tapestry, testament, realm, navigate, boasts, robust, nuanced, multifaceted, intricate, pivotal, crucial, vital, foster, underscore, garner, showcase, leverage, seamless, holistic, comprehensive, rich, align with, resonate, vibrant, stark, meticulous, ever-evolving, treasure trove, game-changer, in the realm of, when it comes to, it's worth noting, it's important to note. (Diction shifts by model generation, but the *flavor* — fluent, elevated, generic — persists.) **Fix:** plain synonyms or cut.

**B2. "Not X, but Y" / "Not only X, but also Y."** Manufactured contrast for false depth. **Fix:** state Y plainly.

**B3. Elegant variation / forced lexical diversity.** Repetition-penalty makes the model rename the same thing repeatedly to avoid reuse ("the artist… the painter… the creator… the visionary"), producing unnatural synonym-cycling. **Fix:** use the plain repeated noun; repetition is fine.

**B4. Excessive hedging / both-sides padding.** "While some may argue… others contend…" balancing where no real controversy exists. **Fix:** cut to the substantive point.

**B5. Em-dash overuse.** Dramatic asides set off by em dashes at high frequency. **Fix:** vary punctuation.

**B6. Overuse of transitional adverbs.** Additionally, Moreover, Furthermore, Consequently, Notably, Importantly opening many sentences. **Fix:** thin them out; let sentences connect by content.

---

## C. Style tells

**C1. Title Case headings.** Capitalizing Every Main Word. **Fix:** sentence case unless house style dictates.

**C2. Overuse of boldface.** Bold scattered mid-sentence for emphasis. **Fix:** reserve for true labels/defined terms.

**C3. Curly/directional quotation marks** where the document otherwise uses straight quotes — a paste tell. **Fix:** match surrounding style.

**C4. Emoji as formatting.** Emoji prefixing headings or bullets (✅, 🚀, 📌). **Fix:** remove in formal/encyclopedic/professional contexts.

**C5. Section titles in plain text.** Output broken into pseudo-sections with bare title lines ("Importance of Thorough Research") that aren't real headings. **Fix:** integrate into prose or use real headings.

---

## D. Communication / chatbot-leakage tells

**D1. Collaborative communication aimed at a user.** "Certainly!", "I hope this helps!", "Here's a draft…", "Would you like me to expand?", "Let me know if…". **Fix:** strip entirely.

**D2. Self-reference / "as an AI."** Any mention of being a model, assistant, or language model. **Fix:** delete.

**D3. Knowledge-cutoff disclaimers & speculation about source gaps.** "As of my last update," "up to my last training update," "while specific details are limited/scarce," "information may not be current." **Fix:** delete; if a fact is genuinely uncertain, verify it instead of hedging.

**D4. Prompt-refusal artifacts.** Leftover safety/refusal language ("I cannot assist with that," "as an AI I'm unable to…") embedded in a deliverable. **Fix:** remove.

**D5. Letter-like writing in the wrong place.** Salutations and valedictions ("Dear editors," "Best regards") on content that isn't a letter (e.g., a wiki talk message or a doc). **Fix:** drop the epistolary frame.

---

## E. Markup tells

**E1. Markdown bleeding into a non-Markdown target.** `**bold**`, `## headings`, `- bullets`, `[text](url)` pasted into wikitext, plain-text email, or a CMS that doesn't render it. **Fix:** convert to the target's real markup or remove.

**E2. Broken / placeholder markup.** Malformed links, leftover `[[ ]]` or `{{ }}` fragments, `[insert citation]`, `[Source]`, `[Year]` placeholders. **Fix:** complete or remove.

---

## F. Citation tells

**F1. Fabricated sources.** Plausible-looking but nonexistent books, articles, DOIs, or URLs. **Fix:** verify each; remove any you can't confirm.

**F2. Broken external links.** Multiple 404s / dead domains in a new piece — strong AI signal. **Fix:** verify links resolve and support the claim.

**F3. Misattributed claims.** A real source named, but it doesn't actually say what's attributed to it (common with RAG models: "Roger Ebert highlighted the lasting influence…"). **Fix:** check the source actually supports the sentence.

**F4. Over-citation of trivia.** Inline-citing uncontroversial or trivial facts a human would leave unsourced, often echoing guideline wording. **Fix:** normalize to sensible citation density.

---

## Verdict guidance

Weigh clusters, not isolated hits. Categorize the result:
- **Clean:** at most a couple of incidental tics; no puffery, real specificity throughout.
- **Light slop:** scattered filler words or a trailing clause or two; fixable in place.
- **Heavy slop:** puffery + rule-of-three + vague attribution recurring; the prose is fluent but largely contentless. The honest fix is often to rebuild around real facts, not to reword.

Always remember: the surface phrases are symptoms. The disease is content that says nothing while sounding important. Treat the disease.
