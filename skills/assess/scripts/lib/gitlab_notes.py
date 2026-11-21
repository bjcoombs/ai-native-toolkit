"""GitLab merge-request notes: AI review, human comment and first human comment (#716).

``review_reality`` on GitLab reads each sampled merge request's notes through
:func:`mr_note_facts` and keeps only the facts below. Note bodies and account
names are read in memory and never leave this module: the read bypasses the
``--platform`` response cache (``glab_api(..., cache=False)``), so no note body
is written to disk, and :class:`NoteFacts` carries booleans and one timestamp.

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
3. **AI review by account.** The author's username is in the ``agent`` or
   ``token_user`` bucket of ``agent_identity.AuthorMatcher.classify_username``
   (the ``[ai_usage]`` rules, #695): a GitLab project or group access-token user
   (``project_<id>_bot_<hash>``) is recognised by its generated username form,
   the token type, never by a display name. A username in ``delivery.AI_REVIEWERS``
   (the pinned AI-review apps, ``[bot]`` dropped) is AI review too. AI review is
   bot review too.
4. **Dependency bot** (Renovate, Dependabot): neither review nor a human comment,
   even when its account carries ``bot: true``.
5. **Other bot**: an author object with ``bot: true`` (a GitLab internal bot) is
   bot review only.
6. **Human comment**: anyone else. The first one's ``created_at`` is
   ``first_human_comment_at``.

``review_reality`` counts a merge request reviewed on a human comment, bot review
or an approval by another account; ``delivery`` takes ``ai_review`` and
``first_human_comment_at``. :func:`gitlab_note_facts` is the private registry spec
that reads every sampled merge request's notes once for both.

Pure except :func:`read_notes`; imports no orchestrator.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, NamedTuple

from lib.agent_identity import AGENT, DEPENDENCY_BOT, TOKEN_USER, AuthorMatcher
from lib.ai_usage import matcher_from_config
from lib.delivery import AI_REVIEWERS
from lib.fleet_config import FleetConfig, review_section
from lib.gh_cli import GhUnavailable, unavailable
from lib.gitlab_cli import GitlabProject, glab_api
from lib.merged_sample import MergedSample, SampleOrReason

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
    ai_review: bool | None            # an AI-review marker, an agent or an access-token user
    bot_review: bool | None           # ai_review, or a ``bot: true`` account
    first_human_comment_at: str | None  # ISO ``created_at`` of the first human comment


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

    def is_ai(self, author: Any) -> bool:
        """An agent, a GitLab access-token user or a pinned AI-review app, by username."""
        name = author.get("username") if isinstance(author, dict) else None
        if not isinstance(name, str) or not name:
            return False
        bare = name.lower().removesuffix("[bot]")
        return bare in AI_REVIEWERS \
            or self.matcher.classify_username(name).bucket in (AGENT, TOKEN_USER)

    @staticmethod
    def is_bot(author: Any) -> bool:
        return isinstance(author, dict) and author.get("bot") is True

    def is_dependency_bot(self, author: Any) -> bool:
        name = author.get("username") if isinstance(author, dict) else None
        return isinstance(name, str) and bool(name) \
            and self.matcher.classify_username(name).bucket == DEPENDENCY_BOT


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
    the read may have stopped before the last note; then an undecided fact is None."""
    human = ai = bot = False
    first_human: str | None = None
    for note in notes:
        if not isinstance(note, dict) or note.get("system") is True:
            continue
        body = note.get("body")
        author = note.get("author")
        name = _username(author)
        if name is not None and name == mr_author:
            continue  # the author's own notes, an agent's on its own change included, are not review
        if (isinstance(body, str) and classifier.has_marker(body)) or classifier.is_ai(author):
            ai = bot = True
            continue
        if classifier.is_dependency_bot(author):
            continue  # before ``bot: true``: a Renovate service account is never review
        if classifier.is_bot(author):
            bot = True
            continue
        if name is None:
            continue
        if not human:
            human = True
            stamp = note.get("created_at")
            first_human = stamp if isinstance(stamp, str) and stamp else None
    return NoteFacts(
        human_comment=human if human or complete else None,
        ai_review=ai if ai or complete else None,
        bot_review=bot if bot or complete else None,
        first_human_comment_at=first_human,
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
    facts = NoteFacts(None, None, None, None)
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
