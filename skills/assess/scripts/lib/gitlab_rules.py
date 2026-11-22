"""Does a GitLab ``rules:`` list let a job, or a pipeline, run on ordinary changes? (issue #884)

``lib.ci_triggers`` asks this of each GitLab job and of a top-level ``workflow: rules:``.
The answer is read per ordinary pipeline: a merge request pipeline
(``CI_PIPELINE_SOURCE == "merge_request_event"``) and a branch push pipeline
(``"push"`` with ``CI_COMMIT_BRANCH`` set and no ``CI_COMMIT_TAG``). Each gives
``yes``, ``no`` or ``unknown``.

GitLab's documented rule semantics (docs.gitlab.com/ci/jobs/job_rules/):

- Rules are read in order and the first rule that matches decides; with no match the job
  (or, for ``workflow:``, the pipeline) is not added.
- A rule matches when its ``if:``, ``changes:`` and ``exists:`` all hold. Its ``when:``
  defaults to the job-level ``when:``, else ``on_success``. ``on_success``, ``always`` and
  ``delayed`` run; ``never``, ``manual`` (added, not run) and ``on_failure`` (no run on a
  passing pipeline) do not. A ``workflow:`` rule is ``always`` or ``never``.
- ``if:`` expressions: ``$VAR`` (defined and not empty), ``==`` / ``!=`` against a
  string, ``null`` or another variable, ``=~`` / ``!~`` against ``/regex/flags``,
  ``&&`` before ``||``, parentheses, and ``!`` (GitLab 18.11). An undefined variable
  compares equal to ``null`` and to another undefined one, and matches a regex as the
  empty string, as GitLab's expression lexemes do.

Each condition reads one of four values in a context: true, false, varies (true for some
pipelines of that kind and false for others: which branch, which files changed, which
merge request) or unknown (a custom variable, a value set in project settings, an
expression this reader cannot parse). A rule that may match with unknown outcome makes
the answer ``unknown``, never ``no``.

Variables: ``CI_PIPELINE_SOURCE``, ``CI_COMMIT_TAG`` (unset in both), ``CI_COMMIT_BRANCH``
(unset in a merge request pipeline), ``CI_MERGE_REQUEST_*`` (unset in a push pipeline),
``CI_OPEN_MERGE_REQUESTS``, ``CI_COMMIT_REF_NAME`` / ``_SLUG``, ``CI_DEFAULT_BRANCH``
and the commit message, title and SHA variables. Any other variable is unknown. A
``CI_PIPELINE_SOURCE`` comparison against ``parent_pipeline``, ``pipeline``,
``external_pull_request_event``, ``external`` or a value GitLab does not list reads
unknown: a child or downstream pipeline can run for an ordinary change.

A rule list that runs the job only through a varying condition gives ``some``, not ``yes``,
so a caller can tell that a ``workflow:`` limited to one branch and a job limited to another
may never meet.

Known limits: a varying rule that runs the job after a varying rule that stopped it reads
``unknown`` (``$CI_COMMIT_BRANCH == "main"`` with ``never``, then ``$CI_COMMIT_BRANCH =~
/feat/``), since this reader does not compare the two; two varying conditions are taken as independent (``$CI_COMMIT_BRANCH ==
"a" && $CI_COMMIT_BRANCH == "b"`` reads varies), regexes are read with Python ``re``
(GitLab uses RE2; a nested quantifier such as ``(a+)+`` reads unknown), ``exists:`` reads unknown, and rule ``variables:`` are not applied.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, NamedTuple

Truth = Literal["true", "false", "varies", "unknown"]
# ``some``: runs in some pipelines of the kind, only through a varying condition (a branch).
Outcome = Literal["yes", "some", "no", "unknown"]
Context = Literal["merge_request", "push"]
CONTEXTS: tuple[Context, ...] = ("merge_request", "push")

_RUN_WHEN = frozenset({"on_success", "always", "delayed"})
_STOP_WHEN = frozenset({"never", "manual", "on_failure"})
_RULE_KEYS = frozenset({
    "if", "when", "changes", "exists", "allow_failure", "variables", "needs", "start_in",
    "interruptible", "auto_cancel",
})
# Sources whose pipelines are not ordinary changes; a comparison against them is exact.
_SPECIAL_SOURCES = frozenset({
    "schedule", "web", "trigger", "api", "chat", "webide", "ondemand_dast_scan",
    "ondemand_dast_validation", "security_orchestration_policy",
})
_SOURCE: Mapping[Context, str] = {"merge_request": "merge_request_event", "push": "push"}
_EXACT_SOURCES = _SPECIAL_SOURCES | set(_SOURCE.values())
# Pipelines that can run for an ordinary change upstream: never read as false.
_UNCERTAIN_SOURCES = ("parent_pipeline", "pipeline", "external_pull_request_event", "external")
_PRESENT_BOTH = frozenset({
    "CI_COMMIT_REF_NAME", "CI_COMMIT_REF_SLUG", "CI_COMMIT_REF_PROTECTED", "CI_DEFAULT_BRANCH",
    "CI_COMMIT_SHA", "CI_COMMIT_SHORT_SHA", "CI_COMMIT_MESSAGE", "CI_COMMIT_TITLE",
    "CI_COMMIT_AUTHOR", "CI_COMMIT_TIMESTAMP",
})
_NULL_BOTH = frozenset({"CI_COMMIT_TAG", "CI_COMMIT_TAG_MESSAGE", "CI_PIPELINE_TRIGGERED"})
_MR_PRESENT = frozenset(f"CI_MERGE_REQUEST_{name}" for name in (
    "ID", "IID", "PROJECT_ID", "PROJECT_PATH", "PROJECT_URL", "REF_PATH", "SOURCE_BRANCH_NAME",
    "TARGET_BRANCH_NAME", "TITLE", "EVENT_TYPE", "DIFF_BASE_SHA", "DIFF_ID",
    "SOURCE_PROJECT_ID", "SOURCE_PROJECT_PATH", "SOURCE_PROJECT_URL",
))

_TOKEN = re.compile(r"""\s*(?:
    (?P<op>&&|\|\||==|!=|=~|!~|!|\(|\))
  | \$\{(?P<bvar>\w+)\}
  | \$(?P<var>\w+)
  | "(?P<dq>(?:\\.|[^"\\])*)"
  | '(?P<sq>(?:\\.|[^'\\])*)'
  | /(?P<re>(?:\\.|[^/\\])*)/(?P<flags>[a-z]*)
  | (?P<null>null)\b
)""", re.VERBOSE)


class _Value(NamedTuple):
    """An operand: ``null``, ``str`` (known text), ``present`` (set, varies), ``maybe``
    (set for some pipelines), ``unknown`` or ``regex``."""

    kind: Literal["null", "str", "present", "maybe", "unknown", "regex"]
    text: str = ""
    flags: str = ""
    name: str = ""


def _variable(name: str, context: Context) -> _Value:
    if name == "CI_PIPELINE_SOURCE":
        return _Value("str", _SOURCE[context], name=name)
    if name in _NULL_BOTH:
        return _Value("null", name=name)
    if name in _PRESENT_BOTH:
        return _Value("present", name=name)
    if name == "CI_COMMIT_BRANCH":
        return _Value("null" if context == "merge_request" else "present", name=name)
    if name == "CI_OPEN_MERGE_REQUESTS":
        return _Value("present" if context == "merge_request" else "maybe", name=name)
    if name.startswith("CI_MERGE_REQUEST_"):
        if context == "push":
            return _Value("null", name=name)
        return _Value("present" if name in _MR_PRESENT else "maybe", name=name)
    return _Value("unknown", name=name)


class _ParseError(Exception):
    pass


def _tokens(text: str) -> list[tuple[str, str, str]]:
    """(kind, text, flags) per token; kind is op, var, str, re or null."""
    out: list[tuple[str, str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise _ParseError(text[pos:])
        pos = match.end()
        if match.group("op"):
            out.append(("op", match.group("op"), ""))
        elif match.group("bvar") or match.group("var"):
            out.append(("var", match.group("bvar") or match.group("var"), ""))
        elif match.group("dq") is not None or match.group("sq") is not None:
            out.append(("str", match.group("dq") if match.group("dq") is not None else match.group("sq"), ""))
        elif match.group("re") is not None:
            out.append(("re", match.group("re"), match.group("flags")))
        else:
            out.append(("null", "", ""))
    return out


_AND_ORDER: tuple[Truth, ...] = ("false", "unknown", "varies")
_OR_ORDER: tuple[Truth, ...] = ("true", "unknown", "varies")
_NOT: Mapping[Truth, Truth] = {"true": "false", "false": "true", "varies": "varies", "unknown": "unknown"}


def _and(values: Sequence[Truth]) -> Truth:
    """Kleene AND: false wins, then unknown, then varies."""
    return next((v for v in _AND_ORDER if v in values), "true")


def _or(values: Sequence[Truth]) -> Truth:
    """Kleene OR: true wins, then unknown, then varies."""
    return next((v for v in _OR_ORDER if v in values), "false")


def _not(value: Truth) -> Truth:
    return _NOT[value]


def _truthy(value: _Value) -> Truth:
    if value.kind == "null":
        return "false"
    if value.kind == "str":
        return "true" if value.text else "false"
    if value.kind == "present":
        return "true"
    return "varies" if value.kind == "maybe" else "unknown"


def _source_guard(left: _Value, right: _Value) -> bool:
    """A ``CI_PIPELINE_SOURCE`` comparison against a source this reader cannot settle.

    It guards ``!=`` as well as ``==``: ``- if: $CI_PIPELINE_SOURCE != "parent_pipeline"`` with
    ``when: never``, then ``- when: always``, is a child-pipeline job, and a true ``!=`` would
    read it as running in neither ordinary pipeline. The cost is ``unknown`` where a running
    ``!=`` rule is true.
    """
    for a, b in ((left, right), (right, left)):
        if a.name == "CI_PIPELINE_SOURCE" and b.kind == "str" and b.text not in _EXACT_SOURCES:
            return True
    return False


def _equals(left: _Value, right: _Value) -> Truth:
    if "unknown" in (left.kind, right.kind) or "regex" in (left.kind, right.kind):
        return "unknown"
    if _source_guard(left, right):
        return "unknown"
    definite = ("null", "str")
    if left.kind in definite and right.kind in definite:
        return "true" if (left.kind, left.text) == (right.kind, right.text) else "false"
    for a, b in ((left, right), (right, left)):
        if a.kind == "present" and (b.kind == "null" or (b.kind == "str" and not b.text)):
            return "false"
    return "varies"


# A quantified group holding a quantifier (``(a+)+``, ``((.*)*)*``): read as unknown.
_NESTED_QUANTIFIER = re.compile(r"[*+}][^()]*\)[*+{]")


def _matches(left: _Value, right: _Value) -> Truth:
    if right.kind != "regex" or left.kind in ("unknown", "regex"):
        return "unknown"
    flags = 0
    for flag in right.flags:
        if flag not in "ims":
            return "unknown"
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}[flag]
    if _NESTED_QUANTIFIER.search(re.sub(r"\\.", "", right.text)):
        return "unknown"  # Python re backtracks without bound on these; GitLab's RE2 does not
    try:
        pattern = re.compile(right.text, flags)
    except re.error:
        return "unknown"
    if left.kind in ("present", "maybe"):
        return "varies"
    text = left.text if left.kind == "str" else ""
    if pattern.search(text):
        return "true"
    if left.name == "CI_PIPELINE_SOURCE" and any(pattern.search(s) for s in _UNCERTAIN_SOURCES):
        return "unknown"
    return "false"


class _Parser:
    """Recursive descent: ``or := and ('||' and)*``, ``and := unary ('&&' unary)*``."""

    def __init__(self, tokens: list[tuple[str, str, str]], context: Context) -> None:
        self.tokens = tokens
        self.pos = 0
        self.context = context

    def _peek(self) -> tuple[str, str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> tuple[str, str, str]:
        token = self._peek()
        if token is None:
            raise _ParseError("end of expression")
        self.pos += 1
        return token

    def parse(self) -> Truth:
        value = self._or()
        if self._peek() is not None:
            raise _ParseError("trailing tokens")
        return value

    def _or(self) -> Truth:
        values = [self._and()]
        while self._peek() == ("op", "||", ""):
            self.pos += 1
            values.append(self._and())
        return _or(values)

    def _and(self) -> Truth:
        values = [self._unary()]
        while self._peek() == ("op", "&&", ""):
            self.pos += 1
            values.append(self._unary())
        return _and(values)

    def _unary(self) -> Truth:
        token = self._peek()
        if token == ("op", "!", ""):
            self.pos += 1
            return _not(self._unary())
        if token == ("op", "(", ""):
            self.pos += 1
            value = self._or()
            if self._take() != ("op", ")", ""):
                raise _ParseError("unclosed parenthesis")
            return value
        left = self._operand()
        token = self._peek()
        if token is None or token[0] != "op" or token[1] not in ("==", "!=", "=~", "!~"):
            return _truthy(left)
        self.pos += 1
        right = self._operand()
        result = _equals(left, right) if token[1] in ("==", "!=") else _matches(left, right)
        return _not(result) if token[1] in ("!=", "!~") else result

    def _operand(self) -> _Value:
        kind, text, flags = self._take()
        if kind == "var":
            return _variable(text, self.context)
        if kind == "str":
            return _Value("str", text)
        if kind == "re":
            return _Value("regex", text, flags)
        if kind == "null":
            return _Value("null")
        raise _ParseError(text)


def evaluate(expression: str, context: Context) -> Truth:
    """The value of one ``rules:if`` expression in a merge request or push pipeline."""
    try:
        return _Parser(_tokens(expression), context).parse()
    except _ParseError:
        return "unknown"


class Rule(NamedTuple):
    """One rules item as read from the file: its keys and inline values."""

    fields: Mapping[str, str]


def _unquote(value: str) -> str | None:
    """A YAML scalar's text, or None when it is a block scalar, a tag or a broken quote."""
    value = value.strip()
    if value[:1] in ("|", ">", "!", "&", "*"):
        return None
    if value[:1] in ("'", '"'):
        if len(value) < 2 or value[-1] != value[0]:
            return None
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('\\"', '"')
    return value


def _readable(rule: Rule) -> bool:
    return bool(rule.fields) and all(key in _RULE_KEYS for key in rule.fields)


def _rule_match(rule: Rule, context: Context) -> Truth:
    fields = rule.fields
    if not _readable(rule):
        return "unknown"
    parts: list[Truth] = []
    if "if" in fields:
        condition = _unquote(fields["if"])
        parts.append("unknown" if condition is None else evaluate(condition, context))
    if "changes" in fields:
        parts.append("varies")
    if "exists" in fields:
        parts.append("unknown")
    return _and(parts)


def outcome(rules: Sequence[Rule], context: Context, default_when: str = "on_success") -> Outcome:
    """Does the first matching rule run the job (or create the pipeline) in this context?"""
    may_run = False
    unknown_stop = False
    varies_stop = False
    for rule in rules:
        match = _rule_match(rule, context)
        if match == "false":
            continue
        raw_when = rule.fields.get("when")
        when = default_when if raw_when is None else (_unquote(raw_when) or "")
        runs: bool | None = True if when in _RUN_WHEN else False if when in _STOP_WHEN else None
        if not _readable(rule):
            runs = None  # a !reference or anchor merge brings its own when:
        if runs is None or match == "unknown":
            if runs is not False:
                may_run = True
            if runs is not True:
                unknown_stop = True
            if match == "true" and runs is None:
                return "unknown"
            continue
        if runs:
            if match == "true":
                return "unknown" if unknown_stop else "some" if varies_stop else "yes"
            return "unknown" if unknown_stop or varies_stop else "some"
        if match == "true":
            return "unknown" if may_run else "no"
        varies_stop = True
    return "unknown" if may_run else "no"
