"""A plain-English lint for the fixed lines the /assess report pastes verbatim (#1174).

Readers whose first language is not English read the report's fixed lines (the summary at
the top, the call to action, the Step sentences, the gate wording and the finding actions)
least easily, so those lines follow one rule: short sentences in common words, with no idiom
and no phrasal verb. ``lint(text)`` returns a ``PlainIssue`` per sentence that breaks it:

- a sentence of more than ``MAX_WORDS`` words (a sentence ends at ``.``, ``!``, ``?`` or ``;``,
  and a backticked span counts as one word);
- a word or phrase from ``IDIOMS``: idioms and phrasal verbs whose meaning is not the sum of
  their words, matched as whole words, case-insensitively.

``fixed_lines()`` names every fixed line by source and key: ``headline.GATE_CHANGE`` and
``GATE_NOUN``, ``exec_summary.GATE_TASK``, ``assess_diagrams_ladder.STEP_MEANING``,
``keyhole_signals.FINDING_ACTIONS``, and the summary lines and call to action rendered for
every Step of the default model (``exec_summary.summary_lines``, ``call_to_action``), so the
words as printed are checked, not only the tables. ``tests/test_plain_english.py`` asserts the
lint passes on all of them. Machine-read output (run-context keys, reason codes, the badge)
is not prose and is not linted.

``untranslated_tokens(english, translation)`` serves the findings template's translation rule
(the labelled translation under the English summary, #1174): it returns each token of the
English that must appear unchanged in the translation and does not, in order. Those are the
backticked spans (ids, paths, commands, code), the link targets, and the bare file names and
paths (a word with a ``/`` or a file extension). Tests: ``tests/test_plain_english.py``.
"""
from __future__ import annotations

import re
from typing import TypedDict, cast

from lib.level_result_types import LevelResult

__all__ = ["IDIOMS", "MAX_WORDS", "PlainIssue", "fixed_lines", "lint", "untranslated_tokens"]

# Long sentences are the first thing a reader in a second language loses track of.
MAX_WORDS = 25

# Idioms and phrasal verbs a reader in a second language may not know: each one means more
# than its words. Small on purpose: the list names the forms the fixed lines are most likely
# to drift into, not every idiom in English.
IDIOMS = (
    "at the end of the day", "bear in mind", "boils down", "by and large", "carry out",
    "come up with", "figure out", "find out", "going forward", "heads up", "in a nutshell",
    "keep an eye on", "keep track of", "kick off", "look into", "low-hanging fruit", "moving forward",
    "on the fly", "out of the box", "pick up", "point out", "rule of thumb", "set up",
    "sort out", "take care of", "under the hood", "work out", "moved on", "moves on",
)

_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
# Sentence marks inside a code span are hidden while splitting, so code never ends a sentence.
_HIDE = str.maketrans({".": "\0", "!": "\1", "?": "\2", ";": "\3"})
_SHOW = str.maketrans({"\0": ".", "\1": "!", "\2": "?", "\3": ";"})
_CODE = re.compile(r"`[^`]*`")


class PlainIssue(TypedDict):
    """One sentence that breaks the plain-English rule."""

    sentence: str
    reason: str  # "too_long" or "idiom:<the idiom>"


def _words(sentence: str) -> int:
    return len(_CODE.sub("CODE", sentence).split())


def lint(text: str) -> list[PlainIssue]:
    """Each sentence of ``text`` that is too long or holds an idiom, in order."""
    issues: list[PlainIssue] = []
    for sentence in (s.strip() for s in _SENTENCE.split(_CODE.sub(lambda m: m.group(0).translate(_HIDE), text))):
        if not sentence:
            continue
        shown = sentence.translate(_SHOW)
        if _words(shown) > MAX_WORDS:
            issues.append({"sentence": shown, "reason": "too_long"})
        prose = _CODE.sub(" ", shown).lower()
        for idiom in IDIOMS:
            words = r"\s+".join(re.escape(w) for w in idiom.split())  # a line wrap is still a space
            if re.search(rf"(?<![\w-]){words}(?![\w-])", prose):
                issues.append({"sentence": shown, "reason": f"idiom:{idiom}"})
    return issues


def _rendered() -> dict[str, str]:
    """The summary lines and call to action as printed, for each Step of the default model."""
    from lib.exec_summary import call_to_action, summary_lines
    from lib.level_tally import default_model
    model = default_model()
    steps = model.get("steps", [])
    out: dict[str, str] = {}
    for s in steps:
        gates = [g for g in s["gates"] if g["id"] not in
                 {x["id"] for p in steps if p["step"] == s["step"] - 1 for x in p["gates"]}]
        blockers = [{"kind": "gate", "gate": g["id"], "condition": f"{g['row']}.{g['level']}", "state": "unmet"}
                    for g in gates]
        level = cast(LevelResult, {"step": s["step"] - 1 or None,
                                   "next_step": {"step": s["step"], "blockers": blockers}})
        if s["step"] > 1:
            for i, line in enumerate(summary_lines(level, None, model)):
                out[f"summary.step{s['step'] - 1}.{i}"] = line
            out[f"call_to_action.step{s['step'] - 1}"] = call_to_action(level)
    top = max((s["step"] for s in steps), default=0)
    if top:
        for i, line in enumerate(summary_lines(cast(LevelResult, {"step": top, "next_step": None}), None, model)):
            out[f"summary.step{top}.{i}"] = line
    return out


def fixed_lines() -> dict[str, str]:
    """Every fixed user-facing line, keyed ``<source>.<key>``."""
    from lib.assess_diagrams_ladder import STEP_MEANING
    from lib.exec_summary import GATE_TASK
    from lib.headline import GATE_CHANGE, GATE_NOUN
    from lib.keyhole.keyhole_signals import FINDING_ACTIONS
    out: dict[str, str] = {}
    for source, table in (("GATE_CHANGE", GATE_CHANGE), ("GATE_NOUN", GATE_NOUN), ("GATE_TASK", GATE_TASK),
                          ("STEP_MEANING", STEP_MEANING), ("FINDING_ACTIONS", FINDING_ACTIONS)):
        out.update({f"{source}.{k}": str(v) for k, v in table.items()})
    out.update(_rendered())
    return out


_LINK_TARGET = re.compile(r"\]\(([^)]+)\)")
_PATHLIKE = re.compile(r"(?<![\w`])([\w.-]+/[\w./-]*[\w]|[\w-]+\.(?:md|py|toml|json|yml|yaml|txt))(?![\w`])")


def untranslated_tokens(english: str, translation: str) -> list[str]:
    """Each id, path, command, code span or link target of ``english`` missing from ``translation``."""
    tokens = [m.group(0) for m in _CODE.finditer(english)]
    tokens += [m.group(1) for m in _LINK_TARGET.finditer(english)]
    prose = _LINK_TARGET.sub("](", _CODE.sub(" ", english))
    tokens += [m.group(1) for m in _PATHLIKE.finditer(prose)]
    # a whole token: AGENTS.md inside OLD_AGENTS.md is not the same file
    return [t for t in dict.fromkeys(tokens)
            if not re.search(rf"(?<![\w./-]){re.escape(t)}(?![\w/-])", translation)]
