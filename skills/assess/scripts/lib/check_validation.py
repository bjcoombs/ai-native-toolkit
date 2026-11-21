"""Structured validation errors with "did you mean" suggestions (#789 design 3.9).

Ported from meridianhub/meridian
``services/control-plane/internal/validator/manifest_validator.go``
(``Severity``, ``ValidationError``, ``ValidationError.Error``, ``findClosestMatch``,
``levenshteinDistance``) at ccd25bb517a87a21f7a0c5c1d67d0be10dbaf228, with the test
tables of ``manifest_validator_test.go`` (``TestLevenshteinDistance``,
``TestFindClosestMatch``); relicensed Apache-2.0 by the copyright holder (grant on
bjcoombs/ai-native-toolkit#789).

A finding is a plain dict, so it serialises as JSON for an authoring loop that reads
the errors and retries: ``severity`` (``error`` blocks, ``warning`` allows), ``path``
(where in the document, ``entries[12].stage``), a machine ``code`` (``UNKNOWN_KEY``),
a human ``message``, and, where one applies, a ``suggestion`` (the closest valid name)
and ``available`` (the valid names). The Go struct's ``line``, ``column``,
``resource_type`` and ``resource_id`` fields are not ported: TOML parse errors carry
their own position, and ``path`` names the entry.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, NotRequired, TypedDict

Severity = Literal["error", "warning"]


class ValidationError(TypedDict):
    """One validation finding (Meridian ``ValidationError``)."""

    severity: Severity
    path: str
    code: str
    message: str
    suggestion: NotRequired[str]
    available: NotRequired[list[str]]


def finding(
    path: str, code: str, message: str, *, severity: Severity = "error",
    suggestion: str = "", available: Iterable[str] = (),
) -> ValidationError:
    """A ``ValidationError``; ``suggestion`` and ``available`` are set only when non-empty."""
    out: ValidationError = {"severity": severity, "path": path, "code": code, "message": message}
    if suggestion:
        out["suggestion"] = suggestion
    names = sorted(available)
    if names:
        out["available"] = names
    return out


def format_error(error: ValidationError) -> str:
    """``[severity] path: message (suggestion: x)``, as Meridian's ``Error()`` renders it."""
    text = f"[{error['severity']}] {error['path']}: {error['message']}"
    if error.get("suggestion"):
        text += f" (suggestion: {error['suggestion']})"
    return text


def errors_only(findings: Iterable[ValidationError]) -> list[ValidationError]:
    """The ``error``-severity findings (Meridian ``addError`` splits the two lists)."""
    return [f for f in findings if f["severity"] == "error"]


def levenshtein(a: str, b: str) -> int:
    """The edit distance between ``a`` and ``b`` (two-row dynamic programme)."""
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for j in range(1, len(b) + 1):
        curr = [j] + [0] * len(a)
        for i in range(1, len(a) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[i] = min(prev[i] + 1, curr[i - 1] + 1, prev[i - 1] + cost)
        prev = curr
    return prev[len(a)]


def closest(target: str, candidates: Iterable[str]) -> str:
    """The candidate nearest ``target`` within half its length, or ``""`` when none is.

    Case-insensitive; the first of equally near candidates wins, as in Meridian's
    ``findClosestMatch``.
    """
    if not target:
        return ""
    best, best_dist = "", len(target) // 2 + 1
    for candidate in candidates:
        dist = levenshtein(target.lower(), candidate.lower())
        if dist < best_dist:
            best, best_dist = candidate, dist
    return best
