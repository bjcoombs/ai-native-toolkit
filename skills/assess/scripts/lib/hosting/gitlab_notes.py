"""GitLab merge-request notes: AI review, human comment and first human comment (#716).

``review_reality`` on GitLab reads each sampled merge request's notes through
:func:`mr_note_facts` and keeps only the facts below. Note bodies are read in
memory and never leave this module: the read bypasses the ``--platform`` response
cache (``glab_api(..., cache=False)``), so no note body is written to disk, and
:class:`NoteFacts` carries booleans, one timestamp and the usernames of unconfirmed
AI hints (#883), which ``delivery`` turns into a count in memory; no name reaches a block.

The read. ``projects/<id>/merge_requests/<iid>/notes?sort=asc&order_by=created_at
&per_page=100`` (then ``&page=2``, ...): oldest first, named by project and
``--hostname`` like every platform call, up to ``NOTES_MAX_PAGES`` pages, stopping at
a short page or once a human comment and AI review are both found. Notes still
undecided after the last page read leave those facts unknown (None), never False.

Each non-system note (``system: true`` notes are GitLab's own events and are
skipped) is classified once, first rule that applies:

1. **The merge request's author**: their own notes are nothing, whatever they
   carry. An agent or access-token user commenting on the change it authored is not
   review (as ``delivery`` rules on GitHub).
2. **AI review by output.** The body carries an AI-review marker: a pinned
   marker (``PINNED_MARKERS``: a line-start "AI Code Review" or "AI Review"
   heading, plain or bold, or an HTML comment naming AI review or CodeRabbit) or
   one of the ``[review] ai_review_markers`` strings from ``--fleet-config``
   (case-insensitive substrings). Whoever else posted it, the note is AI review
   and never a human comment.
3. **AI review by account.** The author's username is in the ``agent`` bucket of
   ``agent_identity.AuthorMatcher.classify_username`` (an account confirmed as an
   agent in ``[identities]``, #765; an ``[ai_usage]`` identity rule; or a ``[bot]``-suffix
   pinned agent, #695). A GitLab project or group access-token user
   (``project_<id>_bot_<hash>``, recognised by that generated form, never a display
   name) lands in the ``agent`` bucket only with ``[ai_usage] token_users_as_agents``.
4. **Automation** (neither review nor a human comment): an access-token user that is
   not an agent and whose note carries no marker (coverage reporters, Danger,
   semantic-release post through tokens; counting them would make review look done
   when nobody looked), a dependency bot (Renovate, Dependabot) and an account
   confirmed as a bot.
4a. **Unconfirmed AI hint** (#883): a username in ``delivery.AI_REVIEWERS`` (``[bot]``
   dropped) or an ``AGENT_HINT_RULES`` login, not in ``[identities]``. GitLab has no bot
   flag, so a person can hold one of those names: the note could be AI review or a
   human comment. With no AI review found, ``ai_review`` is None; with no human comment,
   ``human_comment`` is None; before the first human comment, only
   ``first_human_comment_at`` is None (a person did comment). ``NoteFacts.hints`` keeps the usernames in memory for ``delivery``'s
   ``unconfirmed_ai_accounts`` count. Confirming the account as an agent (AI review) or
   a person (human comment) decides it.
5. **Human comment**: anyone else. The first one's ``created_at`` is
   ``first_human_comment_at``.

GitLab's note ``author`` is a basic user object (id, username, name, state,
avatar and web URLs), with no bot flag, so nothing here reads one: an account is
known only by its username.

``review_reality`` counts a merge request reviewed on a human comment, AI review
or an approval by another account, and its ``bot_review_share`` is the AI-review
share; ``delivery`` takes ``ai_review`` and
``first_human_comment_at``. :func:`gitlab_note_facts` is the private registry spec
that reads every sampled merge request's notes once for both.

Pure except :func:`read_notes`; imports no orchestrator.

GitLab merge-request notes as facts (#716), for `review_reality` and `delivery` (#715). `read_notes(project, iid)` reads
`projects/<id>/merge_requests/<iid>/notes?sort=asc&order_by=created_at&per_page=100`
(`&page=N` after the first; `--hostname` and the project in argv) with `cache=False`;
`mr_note_facts` reads up to `NOTES_MAX_PAGES` (3) pages, stopping at a short page or once
a human comment and AI review are both found, so no note body reaches
the `--platform` response cache. `note_facts(notes, mr_author, classifier, complete)`
returns `NoteFacts(human_comment, ai_review, first_human_comment_at, hints)`; nothing
else of a note leaves the module (`hints`, unconfirmed AI-hint usernames, stay in memory). System notes are skipped. A note is, first rule first:
nothing when the merge request's author posted it (an agent on its own change is not
review); AI review
when its body (cut to `MAX_BODY`) carries a `PINNED_MARKERS` marker (a line-start "AI Code
Review" / "AI Review" heading, plain or bold, emoji allowed, or an HTML comment naming AI
review or CodeRabbit; prose mentioning AI review does not match) or a `[review]
ai_review_markers` string (case-insensitive substring), whoever else posted it; AI review when
its author is in the `agent` bucket of `agent_identity.AuthorMatcher.classify_username`
(confirmed, a config rule or a `[bot]` pinned app); nothing for an unconfirmed name-only AI
match (`is_hint`, #883: a `delivery.AI_REVIEWERS` username, `[bot]` dropped, or an
`AGENT_HINT_RULES` login) leaves AI review undecided (None), a missing human comment
undecided, and the first human comment's time undecided when it precedes it
and is kept in `NoteFacts.hints` (in memory; `delivery` counts them); nothing for an account
confirmed as a bot, automation, a
dependency bot or a `project_<id>_bot_<hash>` / `group_<id>_bot_<hash>` access-token user
(recognised by that form, never a display name) that is not an agent: an unmarked token
note is a coverage reporter, Danger or semantic-release as often as an AI reviewer, so it
counts as AI review only with a marker, an `[ai_usage]` identity rule or `[ai_usage]
token_users_as_agents`; else a human comment, the first giving `first_human_comment_at`.
GitLab's note author is a basic user object with no bot flag, so none is read. With
`complete` False (still a full last page) an undecided fact is None. `NoteFacts` is
`(human_comment, ai_review, first_human_comment_at, hints)`. `classifier_from_config(config)` builds the
classifier from the merged `[ai_usage]` and `[review]` sections. `gitlab_note_facts(sample,
fleet_config)` is the private registry spec (`network=True`, reads `merged_sample` and
`fleet_config`): one `NoteFacts` per sampled merge request in sample order, None off GitLab,
or an unavailable block on a refused read. `review_reality` and `delivery` both read it, so
notes are read once per run; `delivery` fills `first_human_comment_hours_p50/p90`,
`human_commented_share` and `ai_review_share` on GitLab from it. Tests: `tests/test_gitlab_notes.py`, `tests/test_review_reality_gitlab.py`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, NamedTuple

from lib.agent_identity import AGENT, DEPENDENCY_BOT, TOKEN_USER, AuthorMatcher, is_agent_hint
from lib.ai_usage import matcher_from_config
from lib.hosting.delivery import AI_REVIEWERS
from lib.fleet_config import FleetConfig, review_section
from lib.hosting.gh_cli import GhUnavailable, unavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api
from lib.hosting.merged_sample import MergedSample, SampleOrReason

NOTES_PER_PAGE = 100

# Pages of notes read per merge request at most (a long-running merge request with
# an AI review on every pipeline can carry more than one page before a human note).
NOTES_MAX_PAGES = 3

# A note body is cut to this many characters before the markers are matched.
MAX_BODY = 65536

# Markers an AI reviewer prints in its note. Anchored to a heading at line start or
# to the inside of an HTML comment, so prose such as "the AI code review said" in a
# person's comment does not match. Anything else goes in ``[review] ai_review_markers``.
# Spaces, symbols (an emoji) and ``:shortcode:`` emoji between the heading mark and the words.
_PREFIX = r"(?::\w+:|[^\w\n])*"  # [^\w\n] covers spaces and tabs
PINNED_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[ \t]{0,3}#{1,6}" + _PREFIX + r"AI[ \t]+(?:Code[ \t]+)?Review\b",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*\*\*" + _PREFIX + r"AI[ \t]+(?:Code[ \t]+)?Review\b",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"<!--[^>]*?\b(?:AI[-_ ]?(?:Code[-_ ]?)?Review|CodeRabbit)\b[^>]*?-->",
               re.IGNORECASE),
)


class NoteFacts(NamedTuple):
    """What one merge request's notes say; None where up to ``NOTES_MAX_PAGES`` (3) pages /
    300 notes left it undecided."""

    human_comment: bool | None        # a non-system note by a person other than the author
    ai_review: bool | None            # an AI-review marker or an agent
    first_human_comment_at: str | None  # ISO ``created_at`` of the first human comment
    # Unconfirmed name-only AI accounts (``NoteClassifier.is_hint``, #883) that posted a note,
    # lower-cased. In memory only: ``delivery`` publishes the number of distinct accounts.
    hints: frozenset[str] = frozenset()


class NoteClassifier:
    """Classifies one note by its body markers and its author's username."""

    def __init__(self, matcher: AuthorMatcher | None = None,
                 markers: Sequence[str] = ()) -> None:
        self.matcher = matcher or AuthorMatcher()
        self.markers = tuple(m.lower() for m in markers if m)

    def has_marker(self, body: str) -> bool:
        body = body[:MAX_BODY]
        if any(p.search(body) for p in PINNED_MARKERS):
            return True
        lowered = body.lower()
        return any(m in lowered for m in self.markers)

    def _bucket(self, author: Any) -> str | None:
        name = author.get("username") if isinstance(author, dict) else None
        if not isinstance(name, str) or not name:
            return None
        return self.matcher.classify_username(name).bucket

    def is_ai(self, author: Any) -> bool:
        """An agent by username: confirmed in ``[identities]``, an identity rule, a
        ``[bot]``-suffix pinned app, or a token user under ``token_users_as_agents``."""
        return self._bucket(author) == AGENT

    def is_hint(self, author: Any) -> bool:
        """An unconfirmed name-only AI match (#883): a ``delivery.AI_REVIEWERS`` name or an
        ``agent_identity.AGENT_HINT_RULES`` login that is not otherwise an agent."""
        name = author.get("username") if isinstance(author, dict) else None
        if not isinstance(name, str) or not name or self.matcher.confirmed_username(name) is not None:
            return False
        named = name.lower().removesuffix("[bot]") in AI_REVIEWERS or is_agent_hint(username=name)
        return named and self._bucket(author) != AGENT

    def is_automation(self, author: Any) -> bool:
        """Never review, never human: a non-agent access-token user, a dependency bot or an
        account confirmed as a bot."""
        if self._bucket(author) in (TOKEN_USER, DEPENDENCY_BOT):
            return True
        name = author.get("username") if isinstance(author, dict) else None
        return isinstance(name, str) and bool(name) and self.matcher.confirmed_username(name) == "bot"


def classifier_from_config(config: FleetConfig | None) -> NoteClassifier:
    """The note classifier for one repository's merged ``[ai_usage]`` and ``[review]``."""
    markers = review_section(config).get("ai_review_markers", []) if config is not None else []
    return NoteClassifier(matcher_from_config(config), markers)


def _username(actor: Any) -> str | None:
    if isinstance(actor, dict):
        name = actor.get("username")
        if isinstance(name, str) and name:
            return name.lower()
    return None


def note_facts(notes: Sequence[Any], mr_author: str | None, classifier: NoteClassifier,
               complete: bool = True) -> NoteFacts:
    """The facts of one merge request's notes (oldest first). ``complete`` is False when
    the read may have stopped before the last note; then an undecided fact is None. An
    unconfirmed name-only AI account's note (``is_hint``, #883) could be AI review or a
    person's comment, so with no AI review found ``ai_review`` is None; with no human
    comment ``human_comment`` is None, and coming before the first human comment it makes
    only ``first_human_comment_at`` None (a person did comment)."""
    human = ai = False
    first_human: str | None = None
    hints: set[str] = set()
    hint_before_human = False
    for note in notes:
        if not isinstance(note, dict) or note.get("system") is True:
            continue
        body = note.get("body")
        author = note.get("author")
        name = _username(author)
        if name is not None and name == mr_author:
            continue  # the author's own notes, an agent's on its own change included, are not review
        if (isinstance(body, str) and classifier.has_marker(body)) or classifier.is_ai(author):
            ai = True
            continue
        if name is not None and classifier.is_hint(author):
            hints.add(name)
            hint_before_human = hint_before_human or not human
            continue
        if classifier.is_automation(author):
            continue
        if name is None:
            continue
        if not human:
            human = True
            stamp = note.get("created_at")
            first_human = stamp if isinstance(stamp, str) and stamp else None
    return NoteFacts(
        human_comment=True if human else (None if hints or not complete else False),
        ai_review=ai if ai or (complete and not hints) else None,
        first_human_comment_at=None if hint_before_human else first_human,
        hints=frozenset(hints),
    )


def read_notes(project: GitlabProject, iid: int, page: int = 1) -> tuple[list[Any], bool]:
    """One page of a merge request's notes, oldest first, and whether it was the last.

    Never cached: the response holds note bodies (see the module docstring)."""
    more = f"&page={page}" if page > 1 else ""
    got = glab_api(project, f"projects/{project.encoded}/merge_requests/{iid}/notes"
                            f"?sort=asc&order_by=created_at&per_page={NOTES_PER_PAGE}{more}",
                   cache=False)
    if not isinstance(got, list):
        raise GhUnavailable("glab_bad_json: merge request notes were not a list")
    return got, len(got) < NOTES_PER_PAGE


def mr_note_facts(project: GitlabProject, mr: dict[str, Any],
                  classifier: NoteClassifier) -> NoteFacts:
    """Read one merged merge request's notes and keep only their facts: page by page,
    up to ``NOTES_MAX_PAGES``, stopping at a short page or once every fact is decided.

    Raises ``GhUnavailable`` on a refused or malformed read; the caller degrades."""
    iid = mr.get("iid")
    if not isinstance(iid, int):
        raise GhUnavailable("glab_bad_json: a merged merge request has no iid")
    author = _username(mr.get("author"))
    notes: list[Any] = []
    facts = NoteFacts(None, None, None)
    for page in range(1, NOTES_MAX_PAGES + 1):
        got, last = read_notes(project, iid, page)
        notes.extend(got)
        facts = note_facts(notes, author, classifier, complete=last)
        if last or (facts.human_comment and facts.ai_review):
            break
    return facts

# What the registry passes a reader: one ``NoteFacts`` per sampled merge request (in
# the sample's order), None when the sample is not GitLab, or an unavailable block.
NoteFactsOrReason = tuple[NoteFacts, ...] | dict[str, Any] | None


def gitlab_note_facts(sample: SampleOrReason, fleet_config: FleetConfig | None = None) -> NoteFactsOrReason:
    """The private ``gitlab_note_facts`` registry entry: every sampled merge request's
    notes read once, for ``review_reality`` and ``delivery``. Only facts are kept."""
    if not isinstance(sample, MergedSample) or sample.gitlab is None:
        return None
    classifier = classifier_from_config(fleet_config)
    try:
        return tuple(mr_note_facts(sample.gitlab, mr, classifier) for mr in sample.changes)
    except GhUnavailable as e:
        return unavailable(e.reason)
