"""Machine codes of unavailable-block reasons, shared by counts-only output and the tally.

Moved out of ``lib.counts_only.content_free`` (which re-exports both) so ``lib/scoring/level_tally.py``
reads the same codes without importing the scanners' block shapes (#744).

`reason_code` (the `code` of a `code: sentence` reason, else `unavailable`) and `reason_token`
(a known structure code, a `BLOCK_REASON_CODES` code such as `no_ownership_map` or an `agent_outcome` or `concurrency` git code, or the code a known sentence maps to), moved out of `content_free.py`,
which re-exports both, so the tally reads the same codes as counts-only output (#744).
Both read the code through `leading_code`: the text before the first colon, except that
`no_builder` keeps its language, so `no_builder:java: ...` reads `no_builder:java` (#825).
Tests: `tests/test_counts_only.py`, `tests/test_level_tally.py`.
"""
from __future__ import annotations

from lib.counts_only.counts_only_rules import REASON_SENTENCE_CODES, TOKEN_RE
from lib.graph.import_graph import NO_BUILDER, STRUCTURE_REASON_CODES

# The other leading codes a ``REASON`` field keeps: ``structure_drift``'s marker,
# which the full run writes as the headless runner does (#908), and the git-read
# codes of ``agent_outcome`` (``lib.agent_outcome.REASON_CODES``, #1085), which
# ``concurrency`` shares, plus its ``short_history`` (#985).
BLOCK_REASON_CODES: frozenset[str] = frozenset({
    "no_ownership_map",
    "no_git", "git_timeout", "not_a_repo", "shallow_clone", "shallow_unknown", "git_failed",
    "short_history",
})


def leading_code(reason: str) -> str:
    """The code a ``code: sentence`` reason opens with.

    ``no_builder`` carries its language after a colon (``no_builder:java: ...``,
    #825), so it keeps both parts when ``STRUCTURE_REASON_CODES`` lists the pair
    (a known language); any other code ends at the first colon.
    """
    head, _, rest = reason.partition(":")
    head = head.strip()
    pair = f"{head}:{rest.split(':', 1)[0].strip()}"
    return pair if head == NO_BUILDER and pair in STRUCTURE_REASON_CODES else head


def reason_code(reason: object) -> str:
    """The machine code of an unavailable block's reason, or ``"unavailable"``.

    Reasons are written ``code: sentence`` (``no_remote: no github.com remote
    ...``); the code before the colon is kept when it is a token, the sentence
    never is.
    """
    code = leading_code(str(reason)) if reason is not None else ""
    return code if TOKEN_RE.match(code) else "unavailable"


def reason_token(reason: str) -> str | None:
    """A ``REASON`` field's code: the leading code, a known sentence's code, or
    ``"unavailable"``; None (dropped) for an empty reason (#764). A leading
    code is kept only when ``STRUCTURE_REASON_CODES`` or ``BLOCK_REASON_CODES``
    lists it, so a path or a name before the colon never leaks."""
    if not reason.strip():
        return None
    code = leading_code(reason)
    if code in STRUCTURE_REASON_CODES or code in BLOCK_REASON_CODES:
        return code
    return next((c for needle, c in REASON_SENTENCE_CODES if needle in reason),
                "unavailable")
