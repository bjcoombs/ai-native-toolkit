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

``ai_usage``'s author matcher (#695, ``AuthorMatcher`` below) is separate: its
wider pinned list and the ``[ai_usage]`` config rules move only that block. It
also holds the GitLab access-token user recognition (``is_gitlab_token_user``)
and the username form (``classify_username``) the ``--platform`` reads reuse.

The one definition of an agent identity in a git history: `identity_is_agent(email,
name)` (the `[bot]` marker on either, or an `AI_EMAIL_HINTS` e-mail; never a display
name) and `coauthors_have_agent(field)` (any GS-separated `Co-authored-by` value whose
bracketed e-mail is an agent). `is_ai_assistant` / `coauthors_have_ai_assistant` are
the same matcher with non-Copilot `[bot]` accounts removed, for `ai_usage`: a GitHub
squash merge credits every commit author as a co-author, so an automation bot's
fix-up commit must not make a change read as AI-assisted. `COVERED_AGENTS` names the
assistants that counts, in words with no address, for a block's stated limits; keep
it in step with `AI_EMAIL_HINTS`.
Consumers: `change_coupling.authorship_analysis`, `promissory_markers`,
`understanding_analysis` and `ai_usage`. Widening the hints changes all four, so it is
a change of its own.
`ai_usage`'s author matcher (#695) is separate and moves no other block.
`AuthorMatcher(config_rules, token_users_as_agents)` classifies an author into
`agent`, `dependency_bot`, `token_user` or `other`, returning an `AuthorClass(bucket,
rule_index)`. `classify(email, name, username=None)` takes a git author, deriving the
username from a noreply e-mail (`username_from_email`); `classify_username(username)`
takes a platform user (the `--platform` reads, #716/#717). Rules (`IdentityRule`:
a case-insensitive regex searched, plus a field `email`, `username` or `name`; no field
reads e-mail and username) come from `compile_config_rules` over the
`[[ai_usage.agent_identities]]` tables. Pinned defaults (`AGENT_RULES`,
`DEPENDENCY_BOT_RULES`) are written in the username form and never read a display
name; `COVERED_AUTHOR_AGENTS` names them in words. Order: a dependency bot (always,
whatever a rule says), then config rules in order (first match credited), then
pinned agents, then a GitLab access-token user (`is_gitlab_token_user`:
`project_<id>_bot_<hex>`, `group_<id>_bot_<hex>`, by username or its `@noreply.<host>` e-mail; never by display name, which is the
token's owner-chosen name), which is `agent` under `token_users_as_agents` and
`token_user` otherwise. Strings are cut to `MAX_MATCH_LENGTH` before matching (input size only; the fleet
`--timeout` bounds a pathological repository pattern).
Confirmed identities (#765): `AuthorMatcher(..., confirmed=ConfirmedIdentities)` holds the
`[identities]` section as lower-case literal sets (`ConfirmedIdentities.from_section`;
entries are exact accounts, never patterns). Right after the dependency-bot check, an
identity whose `identity_keys(email, name, username)` (the e-mail, the noreply login and
a `[bot]` display name) is a confirmed person or bot is `other`, and a confirmed agent is
`agent` (rule_index None), ahead of every config rule and pinned default; a person or
bot entry wins over an agent entry for the same account.
Tests: `tests/test_ai_usage.py`, `tests/test_ai_usage_authors.py`,
`tests/test_identity_candidates.py`, `tests/test_change_coupling.py`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final, Literal, NamedTuple, cast

AI_EMAIL_HINTS = (
    "[bot]",
    "noreply@anthropic.com",
    "copilot@",
)

# The AI assistants coauthors_have_ai_assistant counts, in words, for a block's
# stated limits. No e-mail address appears here, so a block that carries this
# list carries no identity string. Keep it in step with AI_EMAIL_HINTS.
COVERED_AGENTS = (
    "Claude (Anthropic noreply address)",
    "GitHub Copilot (copilot@ addresses and Copilot [bot] accounts)",
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


def is_ai_assistant(email: str) -> bool:
    """``identity_is_agent`` minus automation bots: an AI assistant's e-mail.

    A ``[bot]`` account counts only when it is Copilot's; Dependabot, Renovate,
    pre-commit-ci and github-actions are automation, not AI assistance.
    """
    e = email.lower()
    if not identity_is_agent(e):
        return False
    return "[bot]" not in e or "copilot" in e


def coauthors_have_ai_assistant(coauthor_field: str) -> bool:
    """True if any Co-Authored-By trailer names an AI assistant (by e-mail)."""
    for value in coauthor_field.split("\x1d"):
        value = value.strip()
        if not value:
            continue
        emails = _BRACKETED_EMAIL.findall(value)
        if is_ai_assistant(emails[0] if emails else value):
            return True
    return False


# --- The ai_usage author matcher (issue #695) --------------------------------
#
# ``ai_usage`` classifies each commit's *author* into one bucket. This matcher is
# its own: ``identity_is_agent`` and ``is_ai_assistant`` above are unchanged, so
# the authorship, promissory-marker and understanding blocks do not move.
#
# A rule is a case-insensitive regular expression (``re.search``) and the field
# it reads. Without a field it reads the e-mail and the username; ``"email"``
# and ``"username"`` read one of them; ``"name"`` reads the display name and is
# an explicit opt-in only a config rule may use ("Claude", "Cursor" and "Codex"
# are human names too, so no pinned default reads it).
#
# Username. A platform user (#716, #717) is matched by username alone
# (``classify_username``). A git author has no username, so one is derived only
# from a platform noreply address (``username_from_email``): GitHub's
# ``[<id>+]login@users.noreply.<host>``, GitLab's ``<id>-login@users.noreply.<host>``
# and a GitLab access-token user's ``project_<id>_bot_<hex>@noreply.<host>``.
# Pinned defaults are written in the username form, so one rule serves a git
# author and a platform user.
#
# Buckets, checked in this order (the first that applies wins):
#
# 1. ``dependency_bot``: Renovate and Dependabot (``DEPENDENCY_BOT_RULES``),
#    never an agent, whatever a config rule says;
# 1a. ``other``: an account the human confirmed as an automation bot or a person
#    (``[identities] bots`` / ``people``, #765), whatever a pinned rule or a
#    config pattern says: a confirmed person never counts as an agent;
# 1b. ``agent``: an account confirmed as an AI agent (``[identities] agents``);
# 2. ``agent``: a config rule, in its order (the commit is credited to the first
#    config rule that matches, so per-rule counts never double-count), then a
#    pinned default (``AGENT_RULES``);
# 3. ``agent`` again for a GitLab access-token user when the config sets
#    ``token_users_as_agents``;
# 4. ``token_user``: a GitLab project or group access-token user
#    (``is_gitlab_token_user``), counted apart and never as an agent;
# 5. ``other``: everyone else, people and automation such as github-actions.
#
# A string longer than ``MAX_MATCH_LENGTH`` is cut before matching. That limits
# the input, not the cost of a pathological pattern (nested quantifiers stay
# exponential); the fleet run's per-repository ``--timeout`` is the backstop, so
# a repository's own pattern can fail only its own row.

RuleField = Literal["email", "username", "name"]
RULE_FIELDS: tuple[RuleField, ...] = ("email", "username", "name")
Bucket = Literal["agent", "dependency_bot", "token_user", "other"]
AGENT: Final = "agent"
DEPENDENCY_BOT: Final = "dependency_bot"
TOKEN_USER: Final = "token_user"
OTHER: Final = "other"

MAX_MATCH_LENGTH = 256


class IdentityRule(NamedTuple):
    pattern: re.Pattern[str]
    field: RuleField | None  # None: the e-mail and the username


def rule(pattern: str, field: RuleField | None = None) -> IdentityRule:
    """A compiled rule; raises ``re.error`` on a bad pattern."""
    return IdentityRule(re.compile(pattern, re.IGNORECASE), field)


# Agent apps and service accounts, matched on username or e-mail, never name.
AGENT_RULES: tuple[IdentityRule, ...] = (
    rule(r"^claude\[bot\]$"),                    # Claude GitHub App
    rule(r"^noreply@anthropic\.com$"),           # Claude Code
    rule(r"^copilot-swe-agent\[bot\]$"),         # GitHub Copilot coding agent
    rule(r"^copilot$", "username"),              # Copilot's noreply login
    rule(r"^copilot@", "email"),                 # Copilot addresses
    rule(r"^devin-ai-integration\[bot\]$"),      # Devin
    rule(r"^google-labs-jules\[bot\]$"),         # Jules
    rule(r"^chatgpt-codex-connector\[bot\]$"),   # OpenAI Codex
    rule(r"^cursoragent@cursor\.com$", "email"),  # Cursor background agent
    rule(r"^openhands@all-hands\.dev$", "email"),  # OpenHands
    rule(r"^openhands-agent$"),                  # OpenHands
    rule(r"^amazon-q-developer\[bot\]$"),        # Amazon Q Developer
)

# The agents AGENT_RULES recognise as commit authors, in words with no address,
# for a block's stated limits. Keep it in step with AGENT_RULES.
COVERED_AUTHOR_AGENTS = (
    "Claude (GitHub App and Claude Code)",
    "GitHub Copilot (coding agent and Copilot accounts)",
    "Devin",
    "Jules",
    "OpenAI Codex",
    "Cursor (background agent)",
    "OpenHands",
    "Amazon Q Developer",
)

# Dependency update bots: a bucket of their own, never an agent.
DEPENDENCY_BOT_RULES: tuple[IdentityRule, ...] = (
    rule(r"^renovate(\[bot\]|-bot)?$"),            # Renovate app, self-hosted renovate-bot
    rule(r"^bot@renovateapp\.com$", "email"),
    rule(r"^renovate(-bot)?@", "email"),
    rule(r"^dependabot(-preview)?\[bot\]$"),
    rule(r"^dependabot$", "username"),
    rule(r"^support@dependabot\.com$", "email"),
)

# GitLab access-token users: ``project_<id>_bot_<hex>`` and ``group_<id>_bot_<hex>``
# (GitLab's "Bot users for projects" / "Bot users for groups").
_TOKEN_USER = re.compile(r"^(?:project|group)_\d+_bot_[0-9a-f]+$", re.IGNORECASE)
_NOREPLY_LOGIN = re.compile(r"^(?:\d+[+-])?([^@+]+)@users\.noreply\.", re.IGNORECASE)


def is_gitlab_token_user(username_or_email: str) -> bool:
    """True for a GitLab project or group access-token user.

    Takes a username (``project_12_bot_4ffc...``) or its generated e-mail
    (``<username>@noreply.<host>``). The display name is the token's name, chosen
    by its owner, so it is never read.
    """
    value = username_or_email.strip()
    local, at, domain = value.partition("@")
    if at and not domain.lower().startswith("noreply."):
        return False
    return bool(_TOKEN_USER.match(local))


def username_from_email(email: str) -> str | None:
    """The platform login a noreply address carries, or None for any other address."""
    e = email.strip()
    if is_gitlab_token_user(e) and "@" in e:
        return e.partition("@")[0]
    m = _NOREPLY_LOGIN.match(e)
    return m.group(1) if m else None


class AuthorClass(NamedTuple):
    bucket: Bucket
    rule_index: int | None  # the config rule that matched, for an agent by config rule


def _rule_matches(r: IdentityRule, email: str, username: str, name: str) -> bool:
    if r.field == "name":
        targets: tuple[str, ...] = (name,)
    elif r.field == "email":
        targets = (email,)
    elif r.field == "username":
        targets = (username,)
    else:
        targets = (email, username)
    return any(t and r.pattern.search(t[:MAX_MATCH_LENGTH]) for t in targets)


def identity_keys(email: str = "", name: str = "", username: str | None = None) -> frozenset[str]:
    """The lower-case forms a confirmed ``[identities]`` entry (#765) may name: the
    e-mail, the login (given, or the one a noreply address carries) and a display
    name only when it carries the reserved ``[bot]`` suffix (``codecov[bot]``)."""
    email = email.strip()
    if username is None:
        username = username_from_email(email) or ""
    keys = {email.lower(), username.strip().lower()}
    if "[bot]" in name.lower():
        keys.add(name.strip().lower())
    return frozenset(k for k in keys if k)


class ConfirmedIdentities(NamedTuple):
    """``[identities]`` (#765) as lower-case literal sets. An entry is an exact
    account, never a pattern, so ``codecov[bot]`` is not a character class."""

    agents: frozenset[str] = frozenset()
    bots: frozenset[str] = frozenset()
    people: frozenset[str] = frozenset()

    @classmethod
    def from_section(cls, section: Mapping[str, object]) -> ConfirmedIdentities:
        """From a validated ``[identities]`` table (lists of strings)."""
        def norm(key: str) -> frozenset[str]:
            values = section.get(key, ())
            items = values if isinstance(values, (list, tuple)) else ()
            return frozenset(v.strip().lower() for v in items if isinstance(v, str) and v.strip())
        return cls(norm("agents"), norm("bots"), norm("people"))

    def class_of(self, keys: frozenset[str]) -> Literal["agent", "bot", "person"] | None:
        """The confirmed class of an identity, or None when unconfirmed. A person or
        bot entry wins over an agent entry for the same account."""
        if keys & self.people:
            return "person"
        if keys & self.bots:
            return "bot"
        if keys & self.agents:
            return "agent"
        return None


class AuthorMatcher:
    """Classify authors into buckets with the pinned defaults plus config rules."""

    def __init__(self, config_rules: tuple[IdentityRule, ...] = (),
                 token_users_as_agents: bool = False,
                 confirmed: ConfirmedIdentities | None = None) -> None:
        self.config_rules = config_rules
        self.token_users_as_agents = token_users_as_agents
        self.confirmed = confirmed or ConfirmedIdentities()

    def classify(self, email: str = "", name: str = "", username: str | None = None) -> AuthorClass:
        """The bucket of one identity. ``username`` defaults to the one ``email`` carries."""
        email = email.strip()
        if username is None:
            username = username_from_email(email) or ""
        if any(_rule_matches(r, email, username, "") for r in DEPENDENCY_BOT_RULES):
            return AuthorClass(DEPENDENCY_BOT, None)
        confirmed = self.confirmed.class_of(identity_keys(email, name, username))
        if confirmed in ("bot", "person"):
            return AuthorClass(OTHER, None)
        if confirmed == "agent":
            return AuthorClass(AGENT, None)
        for i, r in enumerate(self.config_rules):
            if _rule_matches(r, email, username, name):
                return AuthorClass(AGENT, i)
        if any(_rule_matches(r, email, username, "") for r in AGENT_RULES):
            return AuthorClass(AGENT, None)
        if is_gitlab_token_user(username or email):
            return AuthorClass(AGENT if self.token_users_as_agents else TOKEN_USER, None)
        return AuthorClass(OTHER, None)

    def classify_username(self, username: str) -> AuthorClass:
        """The bucket of a platform user known by username alone (#716, #717).

        A config rule with ``field = "email"`` or ``"name"`` cannot match here.
        """
        return self.classify(email="", name="", username=username.strip())


def compile_config_rules(entries: Sequence[Mapping[str, object]]) -> tuple[IdentityRule, ...]:
    """``[[ai_usage.agent_identities]]`` tables (validated) as rules, in order."""
    out: list[IdentityRule] = []
    for entry in entries:
        field = entry.get("field")
        out.append(rule(str(entry["pattern"]), cast(RuleField, field) if field is not None else None))
    return tuple(out)
