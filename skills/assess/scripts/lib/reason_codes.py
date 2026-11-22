"""Machine codes of unavailable-block reasons, shared by counts-only output and the tally.

Moved out of ``lib.counts_only`` (which re-exports both) so ``lib/level_tally.py``
reads the same codes without importing the scanners' block shapes (#744).
"""
from __future__ import annotations

from lib.counts_only_rules import REASON_SENTENCE_CODES, TOKEN_RE
from lib.import_graph import STRUCTURE_REASON_CODES


def reason_code(reason: object) -> str:
    """The machine code of an unavailable block's reason, or ``"unavailable"``.

    Reasons are written ``code: sentence`` (``no_remote: no github.com remote
    ...``); the code before the colon is kept when it is a token, the sentence
    never is.
    """
    code = str(reason).split(":", 1)[0].strip() if reason is not None else ""
    return code if TOKEN_RE.match(code) else "unavailable"


def reason_token(reason: str) -> str | None:
    """A ``REASON`` field's code: the leading code, a known sentence's code, or
    ``"unavailable"``; None (dropped) for an empty reason (#764). A leading
    code is kept only when ``STRUCTURE_REASON_CODES`` lists it, so a path or a
    name before the colon never leaks."""
    if not reason.strip():
        return None
    code = reason.split(":", 1)[0].strip()
    if code in STRUCTURE_REASON_CODES:
        return code
    return next((c for needle, c in REASON_SENTENCE_CODES if needle in reason),
                "unavailable")
