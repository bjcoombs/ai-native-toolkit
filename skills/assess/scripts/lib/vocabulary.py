"""The supportive vocabulary for user-facing output (issue #743, parent #740).

Every report reads as a readiness snapshot, never a verdict on the people who built
the repository. This module is the one source for the words rendered prose leaves
out, the old band labels that stay retired, and the opening line every report
section starts with. ``tests/test_vocabulary_guard.py`` renders the fleet report,
the team next-steps pages, the heat map and the /assess
deterministic report from source and fails, naming the word and where it was
found, when one of them reappears.

Only prose is checked: fenced code, backticked spans, URLs (bare or as a Markdown link
target) and identifiers that contain an underscore are left out (``prose``), and the
retired label "AI-Native" never matches as part of the product name ``ai-native-toolkit``, so machine field names, JSON keys, finding
names and signal paths (``lying_map``, ``instruction_claims.failed``) stay as they
are. Stdlib only.

The one list of words user-facing output leaves out (#743, parent #740): every report reads
as a readiness snapshot, never a verdict on the people who built the repository. A gap is
the next goal. The banned-vocabulary guard is `tests/test_vocabulary_guard.py`
(`cd skills/assess && uv run --locked pytest tests/test_vocabulary_guard.py`). It renders, from
source, a real fleet run with the shipped maturity model (`report.md`, every diagram, the heat
map and the team next-steps pages), the in-repo diagram, trend, heat map and next-steps
fixtures, and the /assess deterministic report with every cross-practice finding. It fails,
naming the word, the output and the line, when one of these words appears in the prose as a
whole word, case-insensitive. Fenced code, backticked spans, URLs (bare, or a Markdown link
target) and identifiers containing an underscore are not prose (`prose`), so machine field
names, JSON keys, finding names, signal paths (`lying_map`, `instruction_claims.failed`,
`fail_under`) and links into the toolkit repository stay unchanged. The retired label
`AI-Native` matches, in any case, only as a whole token not followed by `-toolkit`, because
`ai-native-toolkit` is the product's own name (#794).

| Word | Say instead |
|------|-------------|
| `tangled` | name the measured structure (cross-package edges, modularity) |
| `penalty` | name the size or share the grade reads |
| `weakest` | the row with the most room to grow |
| `weak` | room to grow |
| `lying` | says one thing, does another |
| `missing` | not measured yet, or not in place yet |
| `fail` | stop, or turn the job red |
| `fails` | stops |
| `failed` | could not be scanned, or did not run |
| `failing` | erroring |
| `failure` | an error, or a gap |
| `capped` | held at |
| `unproven` | not yet measured |
| `concerns` | notes |
| `Not Ready` | the Step the repository is ready for |
| `Basic` | the Step name |
| `Solid` | the Step name |
| `AI-Native` | the Step name |

The last four are the retired maturity band labels (`RETIRED_LABELS`); the Step names
(1 Assisted, 2 Supervised agent PRs, 3 Unattended agent PRs, 4 Autonomous loops) replace them.

**Opening line.** Every `## ` section of the fleet `report.md` and every heading of a team
next-steps page (`teams.md` and `teams/<group>.md`) opens with a line that starts
`In place:` (`IN_PLACE`) and says what is already in place before any next step: counts of
what is measured or reached, and at level 0 or unknown a plain "nothing confirmed for this
practice yet", never implied credit. The guard checks it on every rendered page. A team page
and the fleet report open with their strengths named (#1334): the practices held, each with
its count, in place of a counts-only line.

**Step and gate on fleet pages (#1254).** The fleet pages use the /assess words for a
maturity-model entry: its practice, the Step it gates, or the gate. The phrases in
`ROW_PHRASES` (the tuple below holds each one) are checked in every rendered fleet page
(`row_hits`). A table row in the ordinary sense is not checked.

**The model-written template (#1254).** The guard also reads the prose of
`skills/assess-findings/SKILL.md`, fenced template included, and of `agents/assess-layer-scorer.md`,
whose Evidence cells reach the report, and of the repository `README.md`, and fails on the #1254 words there (`TEMPLATE_WORDS`:
lying, capped, unproven, concerns).

**Wording elsewhere.** The quadrant names its shaded corner "Most room to grow: busy, early
levels" in the amber `warn` role, not the red `bad` one; the keyhole summary shows `lying_map` as
"stale doc over complex code" (`keyhole_signals.FINDING_DISPLAY_NAMES`); and the headline words a
reason code it has no entry for with "did not complete" and "not found" in place of "failed" and
"missing" (`headline.reason_word`).

**Colour.** The fleet level ramp (`fleet_svg.LEVEL_RAMP`, the heat map) is the
ColorBrewer YlGn growth palette, pale yellow to deep green, in place of the red OrRd ramp;
unknown cells stay hatched. The complexity treemap and the doc graph keep their own ramps:
they encode complexity and staleness, not maturity.
"""
from __future__ import annotations

import re

#: Critical words and the supportive wording used in their place. Matched as whole
#: words, case-insensitive, so "fail" does not match "failure"; each inflection a
#: renderer could produce is listed on its own.
BANNED_WORDS: dict[str, str] = {
    "tangled": "name the measured structure (cross-package edges, modularity)",
    "penalty": "name the size or share the grade reads",
    "weakest": "the row with the most room to grow",
    "weak": "room to grow",
    "lying": "says one thing, does another",
    "missing": "not measured yet, or not in place yet",
    "fail": "stop, or turn the job red",
    "fails": "stops",
    "failed": "could not be scanned, or did not run",
    "failing": "erroring",
    "failure": "an error, or a gap",
    # #1254: the shared /assess and /fleet report vocabulary.
    "capped": "held at",
    "unproven": "not yet measured",
    "concerns": "notes",
}

#: Fleet pages name a maturity-model entry by its Step, gate or practice, as /assess does,
#: never as a "row" (#1254). A table row in the ordinary sense is unaffected, so only these
#: phrases are checked, on the fleet outputs.
ROW_PHRASES: tuple[str, ...] = ("which row", "team row", "this row", "row's", "rows'", "no row", "unknown rows")

#: The old maturity band labels (#740, #747): the Step names replace them.
RETIRED_LABELS: tuple[str, ...] = ("Not Ready", "Basic", "Solid", "AI-Native")

#: Every section of a fleet ``report.md`` and of a team next-steps page opens with a
#: line that starts with this prefix and says what is already in place, before any
#: next step.
IN_PLACE = "In place:"

_FENCE = re.compile(r"```.*?```", re.S)
_TICKS = re.compile(r"`[^`\n]*`")
_IDENT = re.compile(r"\S*_\S*")
# A URL is an address, not prose: a bare one, or a Markdown link target ``](...)``.
_URL = re.compile(r"\]\([^)\s]*\)|<?https?://[^\s)>]+>?")

#: "AI-Native" is also the product's own name in ``ai-native-toolkit``, so the retired
#: label matches as a whole word in any case, hyphen-joined forms included, except when
#: followed by exactly ``-toolkit``.
_AI_NATIVE = re.compile(r"\bAI-Native(?!(?-i:-toolkit)\b)\b", re.I)


def prose(text: str) -> str:
    """``text`` with fenced code, backticked spans, URLs and underscored identifiers blanked."""
    return _IDENT.sub(" ", _URL.sub(" ", _TICKS.sub(" ", _FENCE.sub(" ", text))))


def _pattern(term: str) -> re.Pattern[str]:
    if term == "AI-Native":
        return _AI_NATIVE
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)


_TERMS = tuple((term, _pattern(term)) for term in (*BANNED_WORDS, *RETIRED_LABELS))
_ROWS = tuple((phrase, re.compile(r"\b" + re.escape(phrase) + r"(?:s\b|(?!\w))", re.I)) for phrase in ROW_PHRASES)

#: The #1254 words the guard checks in the model-written report template.
TEMPLATE_WORDS: tuple[str, ...] = ("lying", "capped", "unproven", "concerns")


def banned_hits(text: str) -> list[tuple[str, int, str]]:
    """Each (term, 1-based line, line text) where a banned word or retired label is in the prose."""
    hits: list[tuple[str, int, str]] = []
    for number, line in enumerate(prose(text).splitlines(), start=1):
        for term, pattern in _TERMS:
            if pattern.search(line):
                hits.append((term, number, line.strip()))
    return hits


def row_hits(text: str) -> list[tuple[str, int, str]]:
    """Each (phrase, 1-based line, line text) where a ``ROW_PHRASES`` phrase is in the prose."""
    return [(phrase, number, line.strip())
            for number, line in enumerate(prose(text).splitlines(), start=1)
            for phrase, pattern in _ROWS if pattern.search(line)]


def template_prose(text: str) -> str:
    """A skill template's text with only its fence lines removed, so the report template a
    fenced block holds is read as the prose it renders to."""
    return re.sub(r"(?m)^[ \t]*```.*$", "", text)


def opening_lines(markdown: str, level: str = "#") -> list[tuple[str, str]]:
    """(heading, first non-blank line under it) for each heading of at least ``level``.

    ``level`` is the heading prefix to match: ``"#"`` matches every heading, ``"##"``
    the ``## `` headings only (not ``###``). A heading followed directly by another
    heading, or by nothing, pairs with an empty string.
    """
    lines = markdown.splitlines()
    pairs: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        marks = len(line) - len(line.lstrip("#"))
        if not marks or not line[marks:].startswith(" "):
            continue
        if level != "#" and marks != len(level):
            continue
        following = next((nxt for nxt in lines[i + 1:] if nxt.strip()), "")
        pairs.append((line, "" if following.startswith("#") else following))
    return pairs
