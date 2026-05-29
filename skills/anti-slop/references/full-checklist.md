# Anti-slop: full pattern catalog

The exhaustive checklist behind `SKILL.md`. The twelve high-frequency tells live in `SKILL.md`; this file adds the lower-frequency categories and gives more examples for each. Adapted from Wikipedia's "Signs of AI writing" essay (`Wikipedia:Signs_of_AI_writing`).

Use this for a full audit. For everyday gate-checking, the `SKILL.md` shortlist is enough.

## How to read this list

A single match is noise. A cluster is the signal. Weight the language tells (what the text *says*) above the formatting tells (how it's punctuated or marked up) — formatting is trivially cleaned by anyone, so its absence proves nothing, but its presence alongside language tells confirms the diagnosis.

The root failure is always the same: vague importance standing in for a specific fact. Every fix below is a special case of "add the fact or cut the claim."

---

## Language and tone

### Puffery and significance inflation
Covered as tell #1. Watch for: stands/serves as a testament, plays a pivotal/vital/crucial role, leaves an indelible mark, rich cultural heritage, deeply rooted, a beacon of, cements its status, enduring legacy. Fix: state the fact that would justify the claim, or delete it.

### Editorializing
The text tells the reader how to feel or judges significance in the encyclopedia's own voice: "it is important to note," "it is worth mentioning," "notably," "interestingly," "remarkably," "tragically." Fix: cut the framing word; let the fact carry its own weight.

### Vague attribution / weasel sourcing
Claims attributed to unnamed authorities: "industry reports suggest," "experts say," "studies show," "it is widely regarded as," "many believe," "observers note." No specific source is given because none was checked. Fix: name and cite the actual source, or remove the claim.

### Trailing -ing significance clauses
Covered as tell #2. A participial tail that editorializes: "…, highlighting its importance," "…, reflecting a broader trend," "…, cementing its place in history." Fix: amputate the tail; the factual clause stands alone.

### Rule of three
Covered as tell #3. Tricolons everywhere — three adjectives, three clauses, three examples. Fix: vary the count and rhythm.

### "Not X, but Y" and other manufactured antithesis
Covered as tell #4. Also: "It's not just A, it's B," "more than just a C, it's a D," "far from being E, it is F." Fix: state the real point directly.

### Elegant variation
Compulsively swapping in synonyms to avoid repeating a noun, so one subject acquires three names in a paragraph: "the author… the wordsmith… the literary figure." Human writers repeat the plain word. Fix: pick one term and reuse it.

### Filler and high-density AI diction
Covered as tell #6. Beyond the watchlist there: leverage, utilize (for "use"), facilitate, spearhead, underscore, garner, myriad, plethora, in the realm of, when it comes to, at the end of the day, navigate the complexities/landscape. Fix: plain words or cut.

### Overstated universality and superlatives
"One of the most important," "widely considered," "renowned," "iconic," "world-class," "state-of-the-art," "cutting-edge" applied without evidence. Fix: drop the superlative or back it with a specific, sourced comparison.

---

## Structure

### Promotional or summarizing conclusions
Covered as tell #9. A final paragraph that adds no fact and only restates significance ("In conclusion / Overall / In summary, X remains a testament to…"). Fix: end on the last real fact.

### Grafted-on sections
Boilerplate headings the topic didn't call for: "Challenges and Future Directions," "Legacy and Impact," "Significance," "Conclusion" on a short factual entry. Fix: delete the section or fold any real content into the body.

### Symmetric scaffolding
"From its humble beginnings to its current status…," "From A to B," paired intro/outro sentences that mirror each other, every section the same length and shape. Real writing is lumpier. Fix: let structure follow the material, not a template.

### Section title repeated in plaintext
The section heading is restated as the first words of the paragraph ("History. The history of X begins…"). A formatting artifact of generated outlines. Fix: delete the restatement.

### List-itis
Turning prose into bulleted lists by default, including lists of one or two items, or bullets that are full paragraphs. Fix: use prose for connected ideas; reserve lists for genuinely parallel, scannable items.

---

## Formatting and markup

### Title Case headings and stray boldface
Covered as tell #7. Also all-bold lead-ins on every list item. Fix: sentence case; bold only for defined terms or UI labels.

### Em-dash overuse and curly quotes
Covered as tell #8. Curly/smart quotes and apostrophes pasted into a document that uses straight ones, or mixed within one document. Fix: match the surrounding style; thin out the em dashes.

### Markdown bleeding into the wrong format
Covered as tell #11. `**bold**`, `##`, backticks, or `*` bullets surviving into wikitext, plain email, or a CMS field. Fix: convert to the target's real markup or strip it.

### Emoji as formatting
Bullets or headings decorated with emoji (✅, 🚀, 🔑, 📌) in contexts that don't use them. Fix: remove.

### Inconsistent or American-by-default conventions
Spelling, date format (Month DD, YYYY), or units silently switching to US defaults mid-document when the rest is British/metric. Fix: match the document's established convention.

---

## Citations and facts

### Fabricated or broken references
Covered as tell #12. Plausible-looking but nonexistent sources, dead or invented URLs, fake DOIs/ISBNs, real outlets credited with things they never published, citations whose content doesn't match the claim. Fix: verify each reference exists and supports the sentence; otherwise remove the claim.

### Misattributed quotations
A named person credited with a quote or judgment they never made, often phrased in slop diction ("Roger Ebert praised its enduring influence"). Fix: confirm the quote, or cut it.

### Date and currency tells
"As of [current year]" with no source, present-tense claims that will rot, "in recent years," round-number estimates with false precision. Fix: anchor to a dated, cited fact or qualify honestly.

---

## Chatbot leakage (the dead giveaways)

These mean text was pasted straight from a chat session without editing. Any one of them is conclusive on its own.

- Direct address to a user: "I hope this helps!", "Certainly!", "Of course!", "Great question."
- Offers to continue: "Would you like me to…", "Let me know if you'd like me to expand / add more / adjust the tone."
- Self-reference: "As an AI language model," "As a large language model, I…," "I cannot browse the internet."
- Knowledge-cutoff disclaimers: "As of my last update," "my training data."
- Refusal / safety artifacts stranded in the output: "I'm sorry, but I can't…," "It's important to approach this topic respectfully."
- Prompt echo: restating the instruction it was given ("Sure, here is a 500-word article about…").

Fix: delete every trace. The deliverable is the prose, never a message about the prose.

---

## Letter-like and talk-page writing

On collaborative or reference text, AI sometimes adopts a correspondence register: greetings ("Dear reader,"), sign-offs, "I would like to discuss," first-person editorializing on a talk page that reads like an essay rather than a focused comment, or excessive politeness and hedging ("I humbly suggest," "with respect"). Fix: match the register of the medium — encyclopedic for articles, terse and specific for discussion threads.

---

## Audit verdict template

Close every audit with a one-line diagnosis, not a score:

- "Heavy slop — puffery and rule-of-three throughout; three sentences carry no fact and should be cut, not reworded."
- "Moderate — clean facts, but every section ends in a significance tail and the conclusion is pure peroration."
- "Mostly clean — two trailing-participle clauses and curly quotes; five-minute fix."

If stripping the slop would leave almost nothing, that emptiness is the finding. Say so plainly rather than polishing a hollow draft.
