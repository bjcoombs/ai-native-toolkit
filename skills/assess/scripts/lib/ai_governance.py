"""AI governance: a policy CI enforces, a policy only declared, and a Copilot review opt-out (issue #851).

Three readings of the committed files, each kept apart because only one of them is evidence that
a rule holds. Nothing is executed; files are read as text, and a CI step is followed into the
repository only through the shared CI-step follower (``lib.ci.ci_step_follower``) or, for a
``python``, ``node`` or shell script a ``run:`` line names, through the same repo-local reader
(``lib.ci.ci_step_paths.local_reader``), so nothing outside the checkout is read.

- **Enforced (counted).** ``enforced`` is true when a GitHub Actions workflow whose ``on:`` names
  ``pull_request`` or ``pull_request_target`` has one step whose code reads the pull request body
  or the commit messages and trailers, tests what it read for an AI disclosure or attribution
  marker, and on that test turns the run red or labels the pull request. The step's code is its
  ``run:`` and ``script:`` values (local actions inlined) and the files its lines lead to
  (``step_texts``, ``enforcing_steps``), so a body read in one step and an exit in another never
  combine; only the read may come from an ``env:`` block of the workflow or the step's own job.
  The three are related by structure, not by line (``lib.ci.disclosure_reader``): shell through
  ``shlex`` with its ``if``/``||``/``&&`` structure (``lib.ci.disclosure_shell``), Python
  through ``ast`` and JavaScript through a small statement reader (``lib.ci.disclosure_script``).
  The act must follow from the marker test: in its branch, right after it in its ``||``/``&&``
  chain, or later in the same block when the test's branch leaves the run first (a guard
  clause); an act of a command nested in that branch
  does not. Code a reader cannot settle reads as no check, never as a guess. A step under
  ``continue-on-error: true`` (its own or its job's) counts only when it labels, as its exit
  cannot turn the run red. A script a ``run:`` line names is read from the ``cd`` directory
  before it. ``enforcing_workflows`` lists them. Null with ``enforced_reason``
  ``no_pr_workflows`` when no tracked workflow runs on pull requests: a bot or an app could still
  enforce, and this reader cannot see one.
- **Declared (listed, never counted).** ``policy_declared`` is true when an ``AI_POLICY.md`` sits
  at the root, in ``.github/`` or in ``docs/`` (``policy_files``), or when a pull request template
  has a checkbox whose own line names AI and disclosure wording (``disclosure_checkboxes``,
  ``template_files``). Exact paths only, never a heading: a checkbox that names AI with no
  disclosure word, such as a change-type box for agent instruction files, does not count.
- **Opt-out (DECLINED).** ``opt_out_files`` lists the Copilot instruction files that decline
  Copilot review for the whole repository: ``.github/copilot-instructions.md``, or a
  ``.github/instructions/*.md`` whose front matter ``applyTo`` is ``**`` or ``**/*`` and whose
  ``excludeAgent`` does not name ``code-review``. A file declines when one sentence outside code
  fences, soft-wrapped lines joined (``_unwrap``) and split only at sentence ends (a ``;`` does not end
  one), is a whole refusal (``_REFUSAL``): a negation, then review or comment on, then pull
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
(``AiGovernanceBlock``). Tests: ``tests/test_ai_governance_condition.py``, ``tests/test_disclosure_readers.py``.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import NamedTuple, TypedDict

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_masking import IndentTree, parse_lines
from lib.ci.ci_matrix import jobs
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_triggers import workflow_events
from lib.ci.disclosure_reader import JS, PYTHON, SHELL, Unit, env_names, step_acts, step_units
from lib.ci.disclosure_terms import LABEL
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
# A script a run: line names, by the reader its language goes to (Ruby has none, so it reads as no check).
_SCRIPT_ARG = re.compile(r"\b(?:python3?|node|bash|sh)\s+(?:-\S+\s+)*(?:\./)?([\w./-]+\.(?:py|js|mjs|cjs|sh))\b")
_LANGUAGES = {".py": PYTHON, ".js": JS, ".mjs": JS, ".cjs": JS, ".sh": SHELL}
_CD = re.compile(r"\bcd\s+([\w./-]+)\s*(?:&&|;)")
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s+(.*)$", re.MULTILINE)
_AI_WORD = re.compile(r"\bAI\b|(?i:\b(?:llm|copilot|chatgpt|claude|gemini|generative|language model)\b)")
_DISCLOSURE = re.compile(r"\b(?:disclos\w*|assist\w*|generat\w*|written|authored|produced|with the help)\b", _I)
_FENCE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.MULTILINE | re.DOTALL)
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
# Sentence ends only: a `;` joins a scoping clause to its refusal ("Do not comment on code; focus on ...").
_SENTENCE = re.compile(r"[^.!?]+")
# A line that starts its own block in markdown: a list item, a heading, a quote or a table row.
_BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#|>|\|)")
_REFUSAL = re.compile(
    r"^\s*(?:(?:github\s+)?copilot\s*[,:]?\s*)?(?:please\s+)?(?:you\s+)?(?:copilot\s+)?"
    r"(?:do\s+not|don['\u2019]?t|never|must\s+not|should\s+not|shall\s+not|will\s+not|won['\u2019]?t|may\s+not|refuse\s+to|decline\s+to)\s+"
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
    """True when one step's ``text`` (a workflow step, or the code of one) reads a pull request body
    or commit trailers, tests it for an AI marker and turns the run red or labels on that test
    (``lib.ci.disclosure_reader.step_acts``). The read may come from ``env``, the ``env:`` blocks
    around the step."""
    return bool(step_acts(step_units(text), env_names(text) | env_names(env)))


def disclosure_checkboxes(text: str) -> int:
    """The checkbox lines of a pull request template that name AI and a disclosure word."""
    return sum(1 for m in _CHECKBOX.finditer(text) if _AI_WORD.search(m.group(1)) and _DISCLOSURE.search(m.group(1)))


def declines_review(text: str) -> bool:
    """True when one sentence of ``text``, outside front matter and code fences, refuses review
    of the whole repository (``_REFUSAL``)."""
    body = _FENCE.sub("", _FRONT.sub("", text, count=1))
    return any(_REFUSAL.match(s.strip(" \t*_`>-#")) for s in _SENTENCE.findall(_unwrap(body)))


def _unwrap(body: str) -> str:
    """Markdown with soft-wrapped lines joined, so a sentence that wraps is read whole; a blank line,
    a list item, a heading, a quote or a table row still starts a new block (its own sentence)."""
    out: list[str] = []
    for line in body.splitlines():
        if out and line.strip() and out[-1].strip() and not _BLOCK_START.match(line):
            out[-1] += " " + line.strip()
        else:
            out.append(line)
    return ".\n".join(out)


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


class Step(NamedTuple):
    text: str  # the step's own lines
    env: str  # the env: blocks of the workflow and of the step's own job
    masked: bool  # continue-on-error: true on the step or its job, so an exit cannot turn the run red


def _env(tree: IndentTree, lines: list[str], keys: list[int]) -> str:
    return "\n".join(lines[j] for k in keys if tree.lines[k].key == "env" for j in tree.subtree(k))


def _masked(tree: IndentTree, keys: list[int]) -> bool:
    return any(tree.lines[k].key == "continue-on-error" and tree.lines[k].value.strip().strip("'\"").lower() == "true"
               for k in keys)


def step_texts(text: str) -> list[Step]:
    """A GitHub workflow's steps, each with the ``env:`` of the workflow and its own job, where a
    step's ``$BODY`` may be set, read through the shared indentation tree (``lib.ci.ci_masking``),
    so a ``- name:`` in the same column as ``steps:`` is read too."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    workflow_env = _env(tree, lines, [i for i, line in enumerate(tree.lines) if line.structural and line.level == 0])
    out: list[Step] = []
    for job in jobs(tree):
        keys = tree.children(job)
        env, job_masked = workflow_env + "\n" + _env(tree, lines, keys), _masked(tree, keys)
        steps = next((k for k in keys if tree.lines[k].key == "steps"), None)
        for item in tree.items(steps) if steps is not None else []:
            span = range(item[0], max(tree.subtree(k).stop for k in item))
            out.append(Step("\n".join(lines[j] for j in span), env, job_masked or _masked(tree, item)))
    return out


def enforcing_steps(follower: StepFollower, reader: ReadLocal, text: str) -> int:
    """The steps of a workflow that enforce AI disclosure on their own: the read, the marker test and
    the act all in one step's code and the files its lines lead to (the read may come from the
    ``env:`` of the workflow or the step's job). A step under ``continue-on-error: true`` counts only
    when it labels."""
    count = 0
    for step in step_texts(text):
        acts = step_acts(step_units(step.text) + _followed_units(follower, reader, step.text),
                         env_names(step.text) | env_names(step.env))
        if acts and (not step.masked or LABEL in acts):
            count += 1
    return count


def _followed_units(follower: StepFollower, reader: ReadLocal, text: str) -> list[Unit]:
    """The code every line of a step leads to: the shared follower's commands as shell, and a
    script a ``python``, ``node`` or shell line names, read from the ``cd`` directory before it."""
    units: list[Unit] = []
    for line in text.splitlines():
        for found in follower.step(line):
            if found.commands:
                units.append(Unit(SHELL, "\n".join(found.commands)))
        cwd = _CD.search(line)
        for script in _SCRIPT_ARG.findall(line):
            body = reader(posixpath.normpath(posixpath.join(cwd.group(1) if cwd else "", script)))
            if body:
                units.append(Unit(_LANGUAGES[posixpath.splitext(script)[1]], body))
    return units


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
