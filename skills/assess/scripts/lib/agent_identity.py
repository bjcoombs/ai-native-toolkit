"""The one definition of an agent identity in a git history.

Shared by the authorship analysis (``change_coupling.authorship_analysis``), the
promissory-marker velocity clock, the understanding analysis and the AI-assisted
change share (``ai_usage``), so every block that says "agent" means the same
identities.

We classify by e-mail, NEVER by display name: "Claude", "Cursor" and "Codex"
are all real human given names/surnames, and mislabelling a person's work as
agent-generated is the failure mode PRD Open Question 6 tells us to avoid.
`[bot]` is the GitHub Apps convention (dependabot, renovate, github-actions,
copilot all commit as `name[bot]`); the anthropic/copilot addresses cover the
common Co-Authored-By trailers. We deliberately do NOT treat bare
`noreply@github.com` / `users.noreply.github.com` as a bot - those are the
privacy addresses humans use for web-UI commits.

Widening the list (Cursor, Codex, Gemini, Aider) changes the authorship results
as well as the AI-assisted share, so it is a change of its own.
"""
from __future__ import annotations

import re

AI_EMAIL_HINTS = (
    "[bot]",
    "noreply@anthropic.com",
    "copilot@",
)

# The agents AI_EMAIL_HINTS covers, in words, for a block's stated limits. No
# e-mail address appears here, so a block that carries this list carries no
# identity string. Keep it in step with AI_EMAIL_HINTS.
COVERED_AGENTS = (
    "GitHub App accounts ([bot] suffix: Dependabot, Renovate, Copilot coding agent, github-actions)",
    "Claude (Anthropic noreply address)",
    "GitHub Copilot (copilot@ addresses)",
)

_BRACKETED_EMAIL = re.compile(r"<([^>]+)>")


def identity_is_agent(email: str, name: str = "") -> bool:
    """True if an identity belongs to a known bot/agent.

    Matches the ``[bot]`` GitHub Apps marker in either name or e-mail (a reserved
    convention no human carries, so safe to match on the name), plus the
    AI-tool e-mail hints. AI tool *names* ("Claude", "Cursor") are never matched
    - those are real human names too (PRD Open Question 6).
    """
    e = email.lower()
    n = name.lower()
    if "[bot]" in e or "[bot]" in n:
        return True
    return any(hint in e for hint in AI_EMAIL_HINTS)


def coauthors_have_agent(coauthor_field: str) -> bool:
    """True if any Co-Authored-By trailer names an agent (matched on its e-mail).

    ``coauthor_field`` is the trailer values joined by GS (``\\x1d``), as
    ``%(trailers:key=Co-authored-by,valueonly,separator=%x1d)`` prints them.
    """
    # Trailer values look like "Claude <noreply@anthropic.com>"; we match the
    # bracketed e-mail, never the display name.
    for value in coauthor_field.split("\x1d"):
        value = value.strip()
        if not value:
            continue
        emails = _BRACKETED_EMAIL.findall(value)
        target = emails[0] if emails else value
        if identity_is_agent(target):
            return True
    return False
