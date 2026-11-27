"""AI governance: a policy CI enforces, a policy only declared, and a Copilot review opt-out (issue #851).

Three readings of the committed files, each kept apart because only one of them is evidence that
a rule holds. Nothing is executed; files are read as text, and a CI step is followed into the
repository only through the shared CI-step follower (``lib.ci.ci_step_follower``) or, for a
``python``, ``node``, ``ruby`` or shell script a ``run:`` line names, through the same repo-local
reader (``lib.ci.ci_step_paths.local_reader``), so nothing outside the checkout is read.

- **Enforced (counted).** ``enforced`` is true when a GitHub Actions workflow whose ``on:`` names
  ``pull_request`` or ``pull_request_target`` has one step that does all three, in the step's
  own text (local actions inlined) or in a file one of its lines leads to (``step_texts``,
  ``enforcing_steps``), so a body read in one step and an exit in another never combine; only the
  read may come from an ``env:`` block of the workflow or the job: it reads the pull request body or the commit
  messages and trailers (``_READS``: ``pull_request.body``, ``gh pr view --json body``,
  ``git log`` with a body or trailer format, ``git interpret-trailers``, the pull request commits
  API); it names an AI disclosure or attribution marker (``_AI_MARKERS``: a tool or model name,
  ``AI`` as a capitalised word, an ``Assisted-by`` or ``Generated-by`` trailer); and it fails the
  run or labels the pull request (``_ACTS``). ``enforcing_workflows`` lists them. Null with
  ``enforced_reason`` ``no_pr_workflows`` when no tracked workflow runs on pull requests: a bot or
  an app could still enforce, and this reader cannot see one.
- **Declared (listed, never counted).** ``policy_declared`` is true when an ``AI_POLICY.md`` sits
  at the root, in ``.github/`` or in ``docs/`` (``policy_files``), or when a pull request template
  has a checkbox whose own line names AI and disclosure wording (``disclosure_checkboxes``,
  ``template_files``). Exact paths only, never a heading: a checkbox that names AI with no
  disclosure word, such as a change-type box for agent instruction files, does not count.
- **Opt-out (DECLINED).** ``opt_out_files`` lists the Copilot instruction files that decline
  Copilot review for the whole repository: ``.github/copilot-instructions.md``, or a
  ``.github/instructions/*.md`` whose front matter ``applyTo`` is ``**`` or ``**/*`` and whose
  ``excludeAgent`` does not name ``code-review``. A file declines when one sentence outside code
  fences is a whole refusal (``_REFUSAL``): a negation, then review or comment on, then pull
  requests, the code or the repository and nothing else; a bare "Do not review." names nothing,
  so it is not one. "Do not review generated files" names a part of
  the repository, so it is not an opt-out. ``read_copilot_opt_out`` turns the files into a
  ``DeclineRecord`` (source ``copilot_opt_out``) listing ``OPT_OUT_CONDITIONS``, which is empty:
  the model has no condition about Copilot review, and declining another agent's conditions would
  hide results the refusal does not speak to. ``lib/owner_exclusions.py`` carries the record in
  the ``owner_exclusions`` block's ``declines``, the headline shows it as a DECLINED badge, and
  ``level_tally.SOURCE_TAGS`` gives this source no tag, so it never takes a condition out of the
  denominator. No file, no record: absence never declines.

Every value is a bool, a count, a reason code or a repo-relative path, so the block survives
counts-only output with its paths. ``scan_ai_governance(repo_root)`` builds the block
(``AiGovernanceBlock``). Tests: ``tests/test_ai_governance_condition.py``.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import TypedDict

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_triggers import workflow_events
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, read_config, repo_files
from lib.scoring.level_result_types import DeclineRecord

NO_PR_WORKFLOWS = "no_pr_workflows"
COPILOT_OPT_OUT = "copilot_opt_out"
PR_EVENTS = frozenset({"pull_request", "pull_request_target"})
POLICY_PATHS = ("AI_POLICY.md", ".github/AI_POLICY.md", "docs/AI_POLICY.md")
TEMPLATE_PATHS = ("pull_request_template.md", "PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md",
                  ".github/PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md", "docs/PULL_REQUEST_TEMPLATE.md")
TEMPLATE_DIR = ".github/PULL_REQUEST_TEMPLATE/"
COPILOT_INSTRUCTIONS = ".github/copilot-instructions.md"
INSTRUCTIONS_DIR = ".github/instructions/"
# The conditions a Copilot review opt-out declines: none. The model has no condition about Copilot
# review, so the record is shown (the DECLINED badge) and sets nothing aside; level_tally.SOURCE_TAGS
# gives this source no tag, so even a record that listed ids could not take a condition out.
OPT_OUT_CONDITIONS: tuple[str, ...] = ()

_I = re.IGNORECASE
_READS = re.compile(r"pull_request\.body|gh\s+pr\s+view\b[^\n]*--json\s+[\w,]*body|interpret-trailers"
                    r"|%\(trailers|git\s+log\b[^\n]*(?:%B|%b|--format=(?:full|raw|medium))"
                    r"|pulls\.listCommits|pull_request\.commits_url|/pulls/[^\s/]+/commits", _I)
_AI_MARKERS = re.compile(r"\bAI\b|(?i:\b(?:llm|copilot|claude|chatgpt|gpt-?\d|gemini|codex"
                         r"|assisted-by|generated-by|ai-assisted|ai-generated)\b)")
_ACTS = re.compile(r"\bexit\s+1\b|\bexit\s*\(\s*1\s*\)|setFailed\s*\(|process\.exit\s*\(\s*1|sys\.exit\s*\("
                   r"|raise\s+SystemExit|--add-label\b|\baddLabels\b|\badd-labels?\b|::error\b", _I)
_SCRIPT_ARG = re.compile(r"\b(?:python3?|node|ruby|bash|sh)\s+(?:-\S+\s+)*\.?/?([\w./-]+\.(?:py|js|mjs|cjs|rb|sh))\b")
_YAML_COMMENT = re.compile(r"^\s*#.*$", re.MULTILINE)
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s+(.*)$", re.MULTILINE)
_AI_WORD = re.compile(r"\bAI\b|(?i:\b(?:llm|copilot|chatgpt|claude|gemini|generative|language model)\b)")
_DISCLOSURE = re.compile(r"\b(?:disclos\w*|assist\w*|generat\w*|written|authored|produced|with the help)\b", _I)
_FENCE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.MULTILINE | re.DOTALL)
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_SENTENCE = re.compile(r"[^.!?\n]+")
_REFUSAL = re.compile(
    r"^\s*(?:(?:github\s+)?copilot\s*[,:]?\s*)?(?:please\s+)?(?:you\s+)?(?:copilot\s+)?"
    r"(?:do\s+not|don'?t|never|must\s+not|should\s+not|shall\s+not|will\s+not|won'?t|may\s+not|refuse\s+to|decline\s+to)\s+"
    r"(?:ever\s+)?(?:review|comment\s+on)"
    r"\s+(?:(?:any|the|this|these|our)\s+)?(?:pull\s+requests?|prs?|code|changes|contributions?|repository|repo|project"
    r"|(?:repository|project)'s\s+(?:pull\s+requests?|prs?|code|changes))"
    r"(?:\s+(?:in|on|for|to)\s+this\s+(?:repository|repo|project))?\s*$", _I)


class AiGovernanceBlock(TypedDict):
    available: bool
    enforced: bool | None  # a PR-triggered workflow reads AI disclosure and fails or labels on it
    enforced_reason: str | None  # NO_PR_WORKFLOWS when null
    enforcing_workflows: list[str]  # the workflows that do, sorted
    policy_declared: bool  # an AI_POLICY.md at a fixed path or a disclosure checkbox: listed, never counted
    policy_files: list[str]  # the AI_POLICY.md files found, sorted
    disclosure_checkboxes: int  # pull request template checkboxes whose own line asks for AI disclosure
    template_files: list[str]  # the templates holding one, sorted
    opt_out_files: list[str]  # the Copilot instruction files that decline Copilot review, sorted


def enforces_disclosure(text: str, env: str = "") -> bool:
    """True when one step's ``text`` reads a pull request body or commit trailers, names an AI
    marker and turns the run red or labels (module docstring). The read may come from ``env``, the
    ``env:`` blocks around the step; the marker and the act must be in the step. Whole-line YAML
    and shell comments are dropped first."""
    clean = _YAML_COMMENT.sub("", text)
    return bool((_READS.search(clean) or _READS.search(_YAML_COMMENT.sub("", env)))
                and _AI_MARKERS.search(clean) and _ACTS.search(clean))


def disclosure_checkboxes(text: str) -> int:
    """The checkbox lines of a pull request template that name AI and a disclosure word."""
    return sum(1 for m in _CHECKBOX.finditer(text) if _AI_WORD.search(m.group(1)) and _DISCLOSURE.search(m.group(1)))


def declines_review(text: str) -> bool:
    """True when one sentence of ``text``, outside front matter and code fences, refuses review
    of the whole repository (``_REFUSAL``)."""
    body = _FENCE.sub("", _FRONT.sub("", text, count=1))
    return any(_REFUSAL.match(s.strip(" \t*_`>-#")) for s in _SENTENCE.findall(body))


def _front_value(text: str, key: str) -> str:
    front = _FRONT.match(text)
    found = re.search(rf"^{key}\s*:\s*(.+)$", front.group(1), re.MULTILINE) if front else None
    return found.group(1).strip().strip("'\"") if found else ""


def repo_wide(rel: str, text: str) -> bool:
    """Whether a Copilot instruction file applies to the whole repository and to code review."""
    if rel == COPILOT_INSTRUCTIONS:
        return True
    return _front_value(text, "applyTo") in ("**", "**/*") and "code-review" not in _front_value(text, "excludeAgent")


def opt_out_files(root: Path, tracked: frozenset[str]) -> list[str]:
    """The Copilot instruction files that decline Copilot review for the whole repository."""
    candidates = [f for f in tracked if f == COPILOT_INSTRUCTIONS
                  or (f.startswith(INSTRUCTIONS_DIR) and f.endswith(".md") and "/" not in f[len(INSTRUCTIONS_DIR):])]
    out: list[str] = []
    for rel in sorted(candidates):
        text = read_config(root, rel)
        if repo_wide(rel, text) and declines_review(text):
            out.append(rel)
    return out


def read_copilot_opt_out(repo_root: Path) -> DeclineRecord | None:
    """The decline record of a Copilot review opt-out, or None when no file declines (absence
    never declines). ``reason`` names the files; ``declined_by`` is unknown, as the file records none."""
    root = repo_root.resolve()
    files = opt_out_files(root, repo_files(root))
    if not files:
        return None
    return {"source": COPILOT_OPT_OUT, "conditions": list(OPT_OUT_CONDITIONS), "declined_by": None,
            "declined_at": None, "version": None, "reason": "Copilot review declined in " + ", ".join(files)}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def step_texts(text: str) -> tuple[list[str], str]:
    """A GitHub workflow's steps, each as its own text, and its ``env:`` blocks outside the steps
    (the workflow's and each job's), where a step's ``$BODY`` may be set."""
    lines = text.splitlines()
    steps: list[str] = []
    env: list[str] = []
    i = 0
    while i < len(lines):
        head = re.match(r"^(\s*)(steps|env):\s*$", lines[i])
        if head is None:
            i += 1
            continue
        base, i, item = len(head.group(1)), i + 1, None
        current: list[str] = []
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > base):
            line = lines[i]
            if head.group(2) == "steps" and line.lstrip().startswith("-"):
                item = _indent(line) if item is None else item
                if _indent(line) == item and current:
                    steps.append("\n".join(current))
                    current = []
            current.append(line)
            i += 1
        if head.group(2) == "env":
            env.extend(current)
        elif current:
            steps.append("\n".join(current))
    return steps, "\n".join(env)


def enforcing_steps(follower: StepFollower, reader: ReadLocal, text: str) -> int:
    """The steps of a workflow that enforce AI disclosure on their own: the read, the marker and the
    act all in one step and the files its lines lead to (the read may come from an ``env:`` block)."""
    steps, env = step_texts(text)
    return sum(1 for step in steps if enforces_disclosure(_followed_text(follower, reader, step), env))


def _followed_text(follower: StepFollower, reader: ReadLocal, text: str) -> str:
    """A step's text with every file its lines lead to appended."""
    parts = [text]
    for line in text.splitlines():
        for found in follower.step(line):
            parts.extend(found.commands)
        for script in _SCRIPT_ARG.findall(line):
            body = reader(posixpath.normpath(script))
            if body:
                parts.append(body)
    return "\n".join(parts)


def scan_ai_governance(repo_root: Path) -> AiGovernanceBlock:
    """The ``ai_governance`` block for the repository at ``repo_root`` (module docstring)."""
    root = repo_root.resolve()
    tracked = repo_files(root)
    reader = local_reader(root, tracked)
    follower = StepFollower(reader, tracked)
    pr_workflows: dict[str, str] = {}
    for rel in ci_config_files(tracked):
        if rel in CI_CONFIG_FILES:
            continue
        text = follow_workflow(read_config(root, rel), reader).text
        if PR_EVENTS & set(workflow_events(text) or []):
            pr_workflows[rel] = text
    enforcing = sorted(rel for rel, text in pr_workflows.items()
                       if enforcing_steps(follower, reader, text) > 0)
    policies = sorted(p for p in POLICY_PATHS if p in tracked)
    templates = [f for f in tracked if f in TEMPLATE_PATHS
                 or (f.startswith(TEMPLATE_DIR) and f.lower().endswith(".md"))]
    boxes = {rel: disclosure_checkboxes(read_config(root, rel)) for rel in sorted(templates)}
    checked = sum(boxes.values())
    return {
        "available": True,
        "enforced": bool(enforcing) if pr_workflows else None,
        "enforced_reason": None if pr_workflows else NO_PR_WORKFLOWS,
        "enforcing_workflows": enforcing,
        "policy_declared": bool(policies) or checked > 0,
        "policy_files": policies,
        "disclosure_checkboxes": checked,
        "template_files": [rel for rel, n in boxes.items() if n],
        "opt_out_files": opt_out_files(root, tracked),
    }
