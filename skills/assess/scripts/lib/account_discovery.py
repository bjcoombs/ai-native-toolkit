"""Bot and agent account discovery: suggestions for the human to confirm (issue #765).

Pinned name lists classify only the unambiguous accounts (``agent_identity``'s
``AGENT_RULES``: agent apps such as ``claude[bot]`` and vendor-owned addresses).
Every other account that looks automated is a *candidate*: it counts as nothing
until the human classifies it in the committed ``[identities]`` section of
``.assess/config.toml`` (or of ``--fleet-config``) as an AI agent, an automation
bot or a person. Unknown earns nothing and costs nothing.

``discover(commits, matcher)`` is pure and deterministic: the same commits and
config give the same candidates in the same order. A commit is a
``HistoryCommit`` (author e-mail, display name, subject, co-author trailers), the
windowed list ``ai_usage`` reads.

Evidence. Each candidate carries the evidence tokens that made it one, in
``EVIDENCE`` order. Strong evidence (``STRONG_EVIDENCE``) makes an account a
candidate; supporting evidence only annotates one, because a person can carry it
too (a GitHub privacy address, a run of same-shaped commit subjects):

- ``bot_suffix``: the reserved ``[bot]`` suffix on the name, e-mail or login;
- ``gitlab_token_user``: a GitLab project or group access-token user;
- ``dependency_bot_hint``: a pinned dependency-bot rule matches (Renovate,
  Dependabot); these stay in their own bucket either way;
- ``agent_name_hint``: an AI tool name (``AI_TOOL_NAMES``) is a word of the
  display name, login or e-mail local part. "Cursor" is a person's name too,
  which is why it is only a hint;
- ``automation_name_hint``: ``bot``, ``robot`` or ``automation`` is a word of
  the display name, login or e-mail local part;
- ``co_author_trailer``: the account appears as a ``Co-authored-by`` trailer
  (supporting);
- ``noreply_address``: a platform noreply address (supporting);
- ``uniform_subjects``: at least ``UNIFORM_MIN_COMMITS`` commits and one subject
  shape (digits and hex runs folded, first four words) covers at least
  ``UNIFORM_SHARE`` of them (supporting).

Not a candidate: an account the matcher already counts as an agent (a pinned
agent rule or an ``[[ai_usage.agent_identities]]`` rule), an account classified
in ``[identities]``, and an account with supporting evidence only.

Privacy. A candidate holds an identity, so it stays local: the listing CLI
(``scripts/identity_candidates.py``) prints it to the terminal and the
``ai_usage`` block carries only the count (``identities_awaiting_confirmation``).
No candidate reaches counts-only output, the fleet run history or an artifact.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple, TypedDict

from lib.agent_identity import (
    AGENT,
    DEPENDENCY_BOT_RULES,
    AuthorMatcher,
    _rule_matches,
    identity_keys,
    is_gitlab_token_user,
    username_from_email,
)

EVIDENCE = (
    "bot_suffix",
    "gitlab_token_user",
    "dependency_bot_hint",
    "agent_name_hint",
    "automation_name_hint",
    "co_author_trailer",
    "noreply_address",
    "uniform_subjects",
)
STRONG_EVIDENCE = frozenset(EVIDENCE[:5])

AI_TOOL_NAMES = (
    "aider", "amazon-q", "anthropic", "chatgpt", "claude", "codeium", "codex", "coderabbit",
    "codegen", "copilot", "cursor", "devin", "gemini", "jules", "openai", "openhands",
    "sourcery", "sweep", "tabnine", "windsurf",
)
_AUTOMATION_WORDS = ("bot", "robot", "automation")
UNIFORM_MIN_COMMITS = 3
UNIFORM_SHARE = 0.8

_NOREPLY = re.compile(r"@(?:users\.)?noreply\.", re.IGNORECASE)
_FOLD = re.compile(r"[0-9a-f]{7,}|\d+", re.IGNORECASE)
_BRACKETED = re.compile(r"^(.*?)\s*<([^>]+)>\s*$")


class HistoryCommit(NamedTuple):
    author_email: str
    author_name: str
    subject: str
    coauthors: str  # the Co-authored-by values joined by GS (``\\x1d``)


class Candidate(TypedDict):
    """One account awaiting confirmation. Local only: never in a run artifact."""

    account: str  # the e-mail, or the login when the e-mail is a noreply address
    name: str
    email: str
    commits: int  # commits authored in the window
    evidence: list[str]  # EVIDENCE tokens, in EVIDENCE order


def _has_word(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered) for w in words)


def _subject_shape(subject: str) -> str:
    return " ".join(_FOLD.sub("#", subject.lower()).split()[:4])


def _uniform(subjects: Sequence[str]) -> bool:
    if len(subjects) < UNIFORM_MIN_COMMITS:
        return False
    shapes: dict[str, int] = {}
    for s in subjects:
        shape = _subject_shape(s)
        shapes[shape] = shapes.get(shape, 0) + 1
    return max(shapes.values()) / len(subjects) >= UNIFORM_SHARE


def evidence_for(email: str, name: str, subjects: Sequence[str] = (),
                 co_author: bool = False) -> list[str]:
    """The evidence tokens one account carries, in ``EVIDENCE`` order."""
    username = username_from_email(email) or ""
    local = email.partition("@")[0]
    texts = (name, username, local)
    found = set()
    if any("[bot]" in t.lower() for t in (name, email)):
        found.add("bot_suffix")
    if is_gitlab_token_user(username or email):
        found.add("gitlab_token_user")
    if any(_rule_matches(r, email, username, "") for r in DEPENDENCY_BOT_RULES):
        found.add("dependency_bot_hint")
    if any(_has_word(t, AI_TOOL_NAMES) for t in texts):
        found.add("agent_name_hint")
    if any(_has_word(t.replace("[bot]", ""), _AUTOMATION_WORDS) for t in texts):
        found.add("automation_name_hint")
    if co_author:
        found.add("co_author_trailer")
    if _NOREPLY.search(email):
        found.add("noreply_address")
    if _uniform(subjects):
        found.add("uniform_subjects")
    return [e for e in EVIDENCE if e in found]


def _co_authors(field: str) -> list[tuple[str, str]]:
    out = []
    for value in field.split("\x1d"):
        m = _BRACKETED.match(value.strip())
        if m:
            out.append((m.group(2).strip(), m.group(1).strip()))
    return out


class _Seen(NamedTuple):
    email: str
    name: str
    subjects: list[str]
    co_author: list[bool]  # one flag, mutable


def discover(commits: Iterable[HistoryCommit], matcher: AuthorMatcher) -> list[Candidate]:
    """The accounts awaiting confirmation, most commits first, then by account."""
    seen: dict[str, _Seen] = {}
    authored: dict[str, int] = {}
    for c in commits:
        key = c.author_email.strip().lower()
        entry = seen.setdefault(key, _Seen(c.author_email.strip(), c.author_name.strip(), [], [False]))
        entry.subjects.append(c.subject)
        authored[key] = authored.get(key, 0) + 1
        for email, name in _co_authors(c.coauthors):
            co = seen.setdefault(email.lower(), _Seen(email, name, [], [False]))
            co.co_author[0] = True
    out: list[Candidate] = []
    for key, s in seen.items():
        if matcher.confirmed.class_of(identity_keys(s.email, s.name)) is not None:
            continue
        if matcher.classify(s.email, s.name).bucket == AGENT:
            continue
        evidence = evidence_for(s.email, s.name, s.subjects, s.co_author[0])
        if not STRONG_EVIDENCE.intersection(evidence):
            continue
        out.append({"account": username_from_email(s.email) or s.email, "name": s.name,
                    "email": s.email, "commits": authored.get(key, 0), "evidence": evidence})
    out.sort(key=lambda c: (-c["commits"], c["account"].lower()))
    return out
