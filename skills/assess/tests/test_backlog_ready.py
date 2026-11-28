"""lib/backlog_ready.py and the backlog-ready level 1 condition (#1322): intake configuration at HEAD."""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.calibration_corpus as cc
import lib.scoring.level_tally as lt
import lib.manifest.check_manifest as cm
from lib.assess_config import load_backlog_ready_labels
from lib.backlog_ready import (READY_LABELS, UNTRIAGED_LABELS, documents_ready_label, form_state,
                               scan_backlog_ready, stale_state)
from lib.backlog_ready_gitlab import READY_SECTIONS, template_has_sections
from lib.counts_only.content_free import counts_only
from lib.scoring.level_signals import DEFAULT_LEVELS, STAGED_BLOCKS, criterion_grade
from lib.scoring.levels import MaturityModel

FIXTURES = Path(__file__).parent / "fixtures" / "backlog_ready"
CID = "backlog-ready.1"
SIGNALS = ("required_form_field", "untriaged_on_open", "ready_label_documented", "stale_job_safe")
FORM = ".github/ISSUE_TEMPLATE/feature.yml"
TRIAGE = ".github/workflows/triage.yml"
STALE = ".github/workflows/stale.yml"
DOC = "CONTRIBUTING.md"


def _repo(tmp_path: Path, files: dict[str, str | None] | None = None) -> Path:
    """The all-met fixture copied to ``tmp_path``, with ``files`` written over it (None deletes)."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "all_met", root)
    for rel, text in (files or {}).items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _read(rel: str) -> str:
    return (FIXTURES / "all_met" / rel).read_text(encoding="utf-8")


def _model() -> MaturityModel:
    """The default model's backlog-ready row alone, with no Steps (placed by #1324)."""
    model = lt.default_model()
    return MaturityModel(version=model["version"], rows=[r for r in model["rows"] if r["id"] == "backlog-ready"])


def _condition(block: Any) -> dict[str, Any]:
    conds = lt.level_result(_model(), {"backlog_ready": block})["conditions"]
    return dict(next(c for c in conds if c["id"] == CID))


def _signals(block: Any) -> tuple[Any, ...]:
    return tuple(block[s] for s in SIGNALS)


# --- success criterion 1: the fixtures ------------------------------------------------

def test_all_four_signals_met(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path))
    assert _signals(block) == (True, True, True, True)
    assert (block["forms"], block["forms_unreadable"], block["workflows"], block["stale_jobs"]) == (1, 0, 2, 1)
    assert block["labels_from_config"] is False
    cond = _condition(block)
    assert (cond["state"], cond["grade"], cond["counted"]) == ("met", "config_only", False)


_NO_REQUIRED = _read(FORM).replace("required: true", "required: false")
_NO_LIST = "# Contributing\n\nAn issue gets the `agent-ready` label when a maintainer agrees.\n"
_CLOSES = _read(STALE).replace('          exempt-issue-labels: "agent-ready,pinned"\n', "")


@pytest.mark.parametrize(("files", "failing"), [
    ({FORM: _NO_REQUIRED}, "required_form_field"),
    ({TRIAGE: None}, "untriaged_on_open"),
    ({DOC: _NO_LIST}, "ready_label_documented"),
    ({STALE: _CLOSES}, "stale_job_safe"),
], ids=SIGNALS)
def test_each_signal_failing_alone(tmp_path: Path, files: dict[str, str | None], failing: str) -> None:
    block = scan_backlog_ready(_repo(tmp_path, files))
    assert {s: block[s] for s in SIGNALS} == {s: s != failing for s in SIGNALS}  # type: ignore[literal-required]
    assert _condition(block)["state"] == "unmet"


@pytest.mark.parametrize("inputs", [
    {"days-before-close": "-1"},
    {"days-before-issue-close": "-1", "days-before-close": "7"},
    {"days-before-issue-stale": "-1"},
    {"exempt-issue-labels": "agent-ready"},
    {"exempt-issue-labels": "pinned, Agent-Ready", "days-before-close": "3"},
    {"only-labels": "needs-info", "days-before-close": "7"},  # scoped away from ready issues
    {"any-of-issue-labels": "question,needs-info"},
])
def test_a_stale_job_that_never_closes_or_exempts_ready_issues_is_safe(inputs: dict[str, str]) -> None:
    assert stale_state(inputs, READY_LABELS) == "safe"


@pytest.mark.parametrize("inputs", [
    {},  # the action's default closes after 7 days
    {"days-before-close": "14", "exempt-issue-labels": "pinned"},
    {"days-before-issue-close": "5", "days-before-close": "-1"},  # the issue setting wins
    {"any-of-labels": "needs-info,agent-ready"},  # the scope takes in ready issues
])
def test_a_stale_job_that_closes_ready_issues_is_unsafe(inputs: dict[str, str]) -> None:
    assert stale_state(inputs, READY_LABELS) == "unsafe"


def test_an_expression_scope_is_unresolved() -> None:
    assert stale_state({"only-labels": "${{ vars.STALE }}"}, READY_LABELS) == "unresolved"


def test_never_closing_and_exempting_stale_jobs_read_safe_in_a_repo(tmp_path: Path) -> None:
    never = _CLOSES.replace("days-before-close: 14", "days-before-close: -1")
    block = scan_backlog_ready(_repo(tmp_path, {STALE: never, ".github/workflows/stale2.yml": _read(STALE)}))
    assert (block["stale_job_safe"], block["stale_jobs"]) == (True, 2)


def test_one_unsafe_stale_job_beside_a_safe_one_is_unsafe(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {".github/workflows/stale2.yml": _CLOSES}))
    assert block["stale_job_safe"] is False


def test_no_stale_job_is_safe(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {STALE: None}))
    assert (block["stale_job_safe"], block["stale_jobs"]) == (True, 0)


def test_an_expression_input_reads_not_measured(tmp_path: Path) -> None:
    expr = _CLOSES.replace("days-before-close: 14", "days-before-close: ${{ vars.CLOSE_DAYS }}")
    block = scan_backlog_ready(_repo(tmp_path, {STALE: expr}))
    assert (block["stale_job_safe"], block["stale_job_safe_reason"]) == (None, "stale_input_unresolved")
    assert (_condition(block)["state"], _condition(block)["reason"]) == ("not_measured", "stale_input_unresolved")


@pytest.mark.parametrize("text", [
    "name: Bug\nbody:\n  - type: input\n    validations: {required: true\n",  # unclosed flow
    "name: Bug\nbody:\n\t- type: input\n",  # a tab indent
    "name: Bug\ndescription: no body\n",  # GitHub requires a body list
])
def test_malformed_form_yaml_reads_not_measured(tmp_path: Path, text: str) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {FORM: text}))
    assert (block["required_form_field"], block["required_form_field_reason"]) == (None, "form_unreadable")
    assert block["forms_unreadable"] == 1
    cond = _condition(block)
    assert (cond["state"], cond["reason"]) == ("not_measured", "form_unreadable")


def test_a_malformed_form_beside_a_required_field_still_meets(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {".github/ISSUE_TEMPLATE/bad.yml": "body: [\n"}))
    assert (block["required_form_field"], block["forms"], block["forms_unreadable"]) == (True, 2, 1)


def test_custom_ready_labels_are_honoured(tmp_path: Path) -> None:
    config = '[backlog_ready]\nready_labels = ["ready-for-agent"]\n'
    root = _repo(tmp_path, {".assess/config.toml": config})
    block = scan_backlog_ready(root)
    # The fixture documents and exempts agent-ready, which the config no longer counts.
    assert (block["ready_label_documented"], block["stale_job_safe"], block["labels_from_config"]) == (False, False, True)
    doc = _read(DOC).replace("agent-ready", "ready-for-agent")
    stale = _read(STALE).replace("agent-ready", "ready-for-agent")
    block = scan_backlog_ready(_repo(tmp_path / "b", {".assess/config.toml": config, DOC: doc, STALE: stale}))
    assert _signals(block) == (True, True, True, True)


def test_custom_untriaged_labels_are_honoured(tmp_path: Path) -> None:
    config = '[backlog_ready]\nuntriaged_labels = ["inbox"]\n'
    assert scan_backlog_ready(_repo(tmp_path, {".assess/config.toml": config}))["untriaged_on_open"] is False
    triage = _read(TRIAGE).replace("needs-triage", "inbox")
    block = scan_backlog_ready(_repo(tmp_path / "b", {".assess/config.toml": config, TRIAGE: triage}))
    assert block["untriaged_on_open"] is True


@pytest.mark.parametrize(("config", "want"), [
    ("", (UNTRIAGED_LABELS, READY_LABELS, False)),
    ("[backlog_ready]\nready_labels = []\n", (UNTRIAGED_LABELS, READY_LABELS, False)),
    ("[backlog_ready]\nready_labels = [\"  \", 3]\n", (UNTRIAGED_LABELS, READY_LABELS, False)),
    ("backlog_ready = 1\n", (UNTRIAGED_LABELS, READY_LABELS, False)),
    ("[backlog_ready]\nuntriaged_labels = [\"inbox\"]\n", (("inbox",), READY_LABELS, True)),
])
def test_the_label_config_degrades_to_the_defaults(tmp_path: Path, config: str,
                                                    want: tuple[Any, ...]) -> None:
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(config, encoding="utf-8")
    assert load_backlog_ready_labels(tmp_path, UNTRIAGED_LABELS, READY_LABELS) == want


# --- the readers ----------------------------------------------------------------------

# --- GitLab (#1406) ------------------------------------------------------------------

GL_TEMPLATE = ".gitlab/issue_templates/Task.md"
GL_CI = ".gitlab-ci.yml"
GL_SECTIONS = "".join(f"## {s}\n\n" for s in READY_SECTIONS)
GL_REFINE = ("refine-new-issues:\n  extends: .glab\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"
             "  script:\n    - |\n      for iid in $(glab issue list --output ids); do\n"
             "        glab issue update \"$iid\" --label needs-triage\n      done\n")
GL_CI_MET = "stages: [test]\n\ntest:\n  script:\n    - make test\n\n.glab:\n  image: glab:1\n\n" + GL_REFINE
GL_DOC = "# Contributing\n\nAn issue is ready when it carries the `refine::ready` scoped label:\n\n- six sections\n"


def _gitlab(tmp_path: Path, files: dict[str, str | None] | None = None) -> Path:
    """A GitLab project meeting level 1, with ``files`` written over it (None leaves one out)."""
    root = tmp_path / "gl"
    merged: dict[str, str | None] = {GL_TEMPLATE: GL_SECTIONS, GL_CI: GL_CI_MET, DOC: GL_DOC, **(files or {})}
    for rel, text in merged.items():
        if text is not None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
    root.mkdir(exist_ok=True)
    return root


def _gl_untriaged(tmp_path: Path, ci: str) -> tuple[Any, Any]:
    block = scan_backlog_ready(_gitlab(tmp_path, {GL_CI: ci}))
    return block["untriaged_on_open"], block.get("untriaged_on_open_reason")


def test_a_gitlab_project_meets_level_1(tmp_path: Path) -> None:
    block = scan_backlog_ready(_gitlab(tmp_path))
    assert _signals(block) == (True, True, True, True)
    assert (block["forms"], block["issue_templates"], block["workflows"]) == (0, 1, 0)
    cond = _condition(block)
    assert (cond["state"], cond["grade"], cond["counted"]) == ("met", "config_only", False)


@pytest.mark.parametrize(("files", "failing"), [
    ({GL_TEMPLATE: GL_SECTIONS.replace("## Root Cause\n", "")}, "required_form_field"),
    ({GL_TEMPLATE: None}, "required_form_field"),
    ({GL_CI: GL_CI_MET.replace(GL_REFINE, "")}, "untriaged_on_open"),
    ({GL_CI: None}, "untriaged_on_open"),
    ({DOC: GL_DOC.replace("`refine::ready`", "ready")}, "ready_label_documented"),
], ids=["section_missing", "no_template", "no_refiner", "no_ci", "no_label"])
def test_each_missing_gitlab_piece_reads_unmet(tmp_path: Path, files: dict[str, str | None], failing: str) -> None:
    block = scan_backlog_ready(_gitlab(tmp_path, files))
    assert {s: block[s] for s in SIGNALS} == {s: s != failing for s in SIGNALS}  # type: ignore[literal-required]
    assert _condition(block)["state"] == "unmet"


@pytest.mark.parametrize(("ci", "reason"), [
    ("test:\n\tscript: [make test]\n", "gitlab_ci_unreadable"),  # a tab indent
    ("test:\n  script: [make test\n", "gitlab_ci_unreadable"),  # an unclosed flow list
    ("include:\n  - project: grp/shared\n    file: ci.yml\n", "gitlab_ci_unsettled"),  # not read offline
])
def test_a_malformed_or_unread_gitlab_ci_reads_not_measured(tmp_path: Path, ci: str, reason: str) -> None:
    block = scan_backlog_ready(_gitlab(tmp_path, {GL_CI: ci}))
    assert (block["untriaged_on_open"], block["untriaged_on_open_reason"]) == (None, reason)
    cond = _condition(block)
    assert (cond["state"], cond["reason"]) == ("not_measured", reason)


@pytest.mark.parametrize("text", [
    GL_SECTIONS,
    "# Task\n\n" + "".join(f"### **{s.upper()}:**\n<!-- fill in -->\n" for s in READY_SECTIONS),
])
def test_a_gitlab_template_with_the_six_sections(text: str) -> None:
    assert template_has_sections(text) is True


@pytest.mark.parametrize("text", [
    GL_SECTIONS.replace("## Impact\n", "Impact\n"),  # not a heading
    "```\n" + GL_SECTIONS + "```\n",  # in fenced code
    "````\n```\n" + GL_SECTIONS + "````\n",  # a shorter fence inside a longer one closes nothing
    "## Summary\n",
])
def test_a_gitlab_template_without_them(text: str) -> None:
    assert template_has_sections(text) is False


def test_a_gitlab_template_in_a_subfolder_is_not_read(tmp_path: Path) -> None:
    block = scan_backlog_ready(_gitlab(tmp_path, {GL_TEMPLATE: None, ".gitlab/issue_templates/x/T.md": GL_SECTIONS}))
    assert (block["required_form_field"], block["issue_templates"]) == (False, 0)


REMOTE = "https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/v4.1.0/templates/refine.yml"


@pytest.mark.parametrize(("ci", "want"), [
    (f"include:\n  - remote: {REMOTE}\n", (True, None)),
    (f"include:\n  - remote: '{REMOTE}'\n    inputs:\n      toolkit_ref: v4.1.0\n", (True, None)),
    (f"include:\n  - remote: {REMOTE}\n    rules:\n      - if: $REFINE\n", (None, "gitlab_ci_unsettled")),
    ("include:\n  - remote: https://example.com/other/templates/refine.yml\n", (None, "gitlab_ci_unsettled")),
    ("include:\n  - template: Jobs/SAST.gitlab-ci.yml\n", (False, None)),  # GitLab's own templates label nothing
])
def test_the_refinement_template_include(tmp_path: Path, ci: str, want: tuple[Any, Any]) -> None:
    assert _gl_untriaged(tmp_path, ci) == want


_SCHED = "  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"


@pytest.mark.parametrize("job", [
    "r:\n" + _SCHED + "  script: glab issue update 1 -l needs-triage\n",
    "r:\n" + _SCHED + "  script:\n    - glab issue update 1 --label=bug,needs-triage\n",
    "r:\n" + _SCHED + "  script:\n    - glab api -X PUT projects/1/issues/2 -f add_labels=needs-triage\n",
    "r:\n" + _SCHED + "  script:\n    - curl --data '{\"add_labels\": \"Needs-Triage\"}' $URL\n",
    "r:\n" + _SCHED + "  script:\n    - glab issue note 1 -m '/label ~\"needs triage\"'\n",
    "r:\n" + _SCHED + "  script:\n    - glab issue update 1 \\\n      --label needs-triage\n",
    # a form-encoded quick action in a curl notes call (#1587)
    "r:\n" + _SCHED + "  script:\n    - curl -X POST --data 'body=/label ~needs-triage' \"$API/issues/2/notes\"\n",
    "r:\n" + _SCHED + "  script: [glab issue update 1 -l needs-triage]\n",  # a flow list (#1587)
    "r:\n" + _SCHED + "  script: [echo, glab issue update 1 -l needs-triage, echo]\n",
    "r:\n  only: [schedules]\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  only:\n    refs:\n      - schedules\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\" && $REFINE == \"true\"\n"
    "  script: glab issue update 1 -l needs-triage\n",  # a schedule's own variable
    "default:\n  before_script:\n    - glab issue update 1 -l needs-triage\nr:\n" + _SCHED + "  script: [echo]\n",
    ".base:\n  script: glab issue update 1 -l needs-triage\nr:\n  extends: .base\n" + _SCHED,
])
def test_a_scheduled_job_that_adds_an_untriaged_label(tmp_path: Path, job: str) -> None:
    assert _gl_untriaged(tmp_path, job) == (True, None)


@pytest.mark.parametrize("job", [
    "r:\n  script: glab issue update 1 -l needs-triage\n",  # not written for a schedule
    "r:\n  rules:\n    - when: always\n  script: glab issue update 1 -l needs-triage\n",  # every pipeline
    ".r:\n" + _SCHED + "  script: glab issue update 1 -l needs-triage\n",  # hidden: never runs alone
    "r:\n" + _SCHED + "  when: manual\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n      when: never\n    - when: always\n"
    "  script: glab issue update 1 -l needs-triage\n",
    "r:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n      when: manual\n"
    "  script: glab issue update 1 -l needs-triage\n",
    "r:\n  only: [schedules]\n  when: never\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  only: [schedules]\n  except: [schedules]\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n" + _SCHED + "  script: glab issue update 1 --unlabel needs-triage\n",  # removes
    "r:\n" + _SCHED + "  script: glab issue list --label needs-triage\n",  # lists
    "r:\n" + _SCHED + "  script: glab api projects/1/issues?labels=needs-triage\n",  # a filter
    "r:\n" + _SCHED + "  script: glab issue update 1 -l needs-triage-later\n",  # another label
    "r:\n" + _SCHED + "  script: echo \"/label ~needs-triage\"\n",  # a quick action posted nowhere
    "r:\n" + _SCHED + "  script:\n    # glab issue update 1 -l needs-triage\n    - echo\n",  # a comment
    "r:\n" + _SCHED + "  script: [echo]\n.x:\n  script: glab issue update 1 -l needs-triage\n",
    "workflow:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n      when: never\n    - when: always\n"
    "r:\n" + _SCHED + "  script: glab issue update 1 -l needs-triage\n",  # no scheduled pipeline
    ".base:\n  script: glab issue update 1 -l needs-triage\nr:\n  extends: .base\n" + _SCHED
    + "  script: [echo]\n",  # the job's own script replaces the template's
    # a ] inside a label is part of it, and a quick action in a title does not run (#1587)
    "r:\n" + _SCHED + "  script: glab issue update 1 -l needs-triage]archived\n",
    "r:\n" + _SCHED + "  script:\n    - curl -X POST --data-urlencode 'title=/label ~needs-triage' \"$API/issues\"\n",
])
def test_a_job_that_does_not_label_new_issues_on_a_schedule(tmp_path: Path, job: str) -> None:
    assert _gl_untriaged(tmp_path, job) == (False, None)


@pytest.mark.parametrize("job", [
    "r:\n" + _SCHED + "  script: glab issue update 1 -l $TRIAGE_LABEL\n",  # a variable label
    "r:\n" + _SCHED + "  script:\n    - !reference [.lab, script]\n",
    "r:\n  rules:\n    - !reference [.sched, rules]\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\" =~ broken\n"
    "  script: glab issue update 1 -l needs-triage\n",  # an expression not read
    ".lab: &lab\n  script: glab issue update 1 -l needs-triage\nr:\n  <<: *lab\n" + _SCHED,  # an anchor merge
    "r:\n  extends: .missing\n" + _SCHED + "  script: glab issue update 1 -l needs-triage\n",
    "r:\n  only: {refs: [schedules]}\n  script: glab issue update 1 -l needs-triage\n",  # a flow map
    "r:\n  only: [schedules]\n  except:\n    variables: [$X]\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n" + _SCHED + "  only: [schedules]\n  script: glab issue update 1 -l needs-triage\n",  # GitLab rejects it
    "r:\n  only: [schedules]\n  when: $WHEN\n  script: glab issue update 1 -l needs-triage\n",
    "r:\n  rules:\n    - if: $REFINE == \"true\"\n  script: glab issue update 1 -l needs-triage\n",  # a schedule's variable
    "r:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\" && $CI_PROJECT_PATH == \"a/b\"\n"
    "  script: glab issue update 1 -l needs-triage\n",  # a predefined variable not modelled
    ".base: &base\n  only: [schedules]\n  script: glab issue update 1 -l needs-triage\nr:\n  <<: *base\n",
    # two schedule variables that may never hold together
    "workflow:\n  rules:\n    - if: $REFINE == \"triage\"\nr:\n  rules:\n"
    "    - if: $CI_PIPELINE_SOURCE == \"schedule\" && $REFINE == \"maintenance\"\n"
    "  script: glab issue update 1 -l needs-triage\n",
    "workflow:\n  rules:\n    - !reference [.flow, rules]\nr:\n" + _SCHED
    + "  script: glab issue update 1 -l needs-triage\n",  # an unread workflow
])
def test_a_labelling_job_this_reader_cannot_settle_reads_not_measured(tmp_path: Path, job: str) -> None:
    assert _gl_untriaged(tmp_path, job) == (None, "gitlab_ci_unsettled")


@pytest.mark.parametrize(("ci", "want"), [
    # a flow list over two lines: valid, though this reader does not follow it
    ("r:\n  only: [main,\n    schedules]\n  script: glab issue update 1 -l needs-triage\n", (None, "gitlab_ci_unsettled")),
    # tabs inside a block scalar (a <<- heredoc) are text, not indentation
    ("r:\n" + _SCHED + "  script:\n    - |\n      cat <<-EOF\n      \t\tbody\n      \tEOF\n"
     "      glab issue update 1 -l needs-triage\n", (True, None)),
])
def test_valid_yaml_is_not_read_as_malformed(tmp_path: Path, ci: str, want: tuple[Any, Any]) -> None:
    assert _gl_untriaged(tmp_path, ci) == want


@pytest.mark.parametrize(("ci", "want"), [
    (f"include:\n  - remote: {REMOTE}\nx:\n\tscript: y\n", (None, "gitlab_ci_unreadable")),  # GitLab rejects it
    (f"include:\n  - remote: {REMOTE}\nworkflow:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"
     "      when: never\n    - when: always\n", (False, None)),  # no scheduled pipeline
    (f"include:\n  - remote: {REMOTE}\nworkflow:\n  rules:\n    - if: $REFINE\n", (None, "gitlab_ci_unsettled")),
    (f"include:\n  - remote: {REMOTE}\nworkflow:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n",
     (True, None)),
])
def test_the_refinement_include_needs_a_pipeline_that_runs_it(tmp_path: Path, ci: str, want: tuple[Any, Any]) -> None:
    assert _gl_untriaged(tmp_path, ci) == want


def test_a_tag_scheduled_refiner_and_a_workflow_any_schedule_meets(tmp_path: Path) -> None:
    ci = ("workflow:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\nr:\n  rules:\n"
          "    - if: $CI_PIPELINE_SOURCE == \"schedule\" && $CI_COMMIT_TAG\n"
          "  script: glab issue update 1 -l needs-triage\n")
    assert _gl_untriaged(tmp_path, ci) == (True, None)


def test_an_unreadable_gitlab_template_reads_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.backlog_ready as br
    real = br.read_config
    monkeypatch.setattr(br, "read_config", lambda root, rel: "" if rel == GL_TEMPLATE else real(root, rel))
    block = scan_backlog_ready(_gitlab(tmp_path))
    reason = "gitlab_template_unreadable"
    assert (block["required_form_field"], block["required_form_field_reason"]) == (None, reason)
    (step,) = [r["next_step"] for r in cm.reason_codes(cm.core_manifest(), "backlog_ready") if r["code"] == reason]
    assert "GitLab" in step and "GitHub" not in step  # the advice names the GitLab case (#1587)
    # an unreadable GitHub form beside it keeps the form code and its GitHub advice
    block = scan_backlog_ready(_gitlab(tmp_path / "b", {FORM: "body: [\n"}))
    assert block["required_form_field_reason"] == "form_unreadable"


def test_an_anchor_merge_in_a_file_that_adds_no_label_is_settled(tmp_path: Path) -> None:
    ci = ".d: &d\n  image: x\nbuild:\n  <<: *d\n  script: [make]\n"
    assert _gl_untriaged(tmp_path, ci) == (False, None)


def test_a_github_workflow_and_a_gitlab_refiner_both_count(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {TRIAGE: None, GL_CI: GL_CI_MET}))
    assert block["untriaged_on_open"] is True
    block = scan_backlog_ready(_repo(tmp_path / "b", {GL_CI: "test:\n\tscript: x\n"}))
    assert block["untriaged_on_open"] is True  # the GitHub workflow meets it; the GitLab file is not needed


@pytest.mark.parametrize(("text", "met"), [
    ("## ~agent-ready\n\n- has criteria\n", True),
    ("Label it ~\"refine::ready\" when:\n\n- it has criteria\n", True),
    ("Label it ~refine::ready when:\n\n- it has criteria\n", True),
    ("## ~ready\n\n- has criteria\n", True),
    ("Label it ~\"ready\" when:\n\n- it has criteria\n", True),  # a quoted one-word reference (#1587)
    ("Label it ~\"Ready\" when:\n\n- it has criteria\n", False),
    ("Label it \"ready\" when:\n\n- it has criteria\n", False),  # a quoted word is not a reference
    ("## ~Ready\n\n- has criteria\n", False),  # a one-word label keeps its case
    ("## ~agent-ready-later\n\n- not the label\n", False),
])
def test_gitlab_label_references_in_documentation(text: str, met: bool) -> None:
    assert documents_ready_label(text, READY_LABELS) is met


def test_an_empty_repository_reads_unmet(tmp_path: Path) -> None:
    block = scan_backlog_ready(tmp_path)
    assert _signals(block) == (False, False, False, True)
    assert _condition(block)["state"] == "unmet"


@pytest.mark.parametrize(("validations", "state"), [
    ("    validations:\n      required: true\n", "required"),
    ("    validations: { required: true }\n", "required"),
    ("    validations:\n      required: false\n", "none"),
    ("    validations:\n      required: \"true\"\n", "none"),  # a string, not a boolean
    ("", "none"),
])
def test_form_required_field_shapes(validations: str, state: str) -> None:
    text = "name: X\nbody:\n  - type: input\n    id: a\n    attributes:\n      label: A\n" + validations
    assert form_state(text) == state


def test_a_required_key_outside_validations_is_not_a_required_field() -> None:
    text = "name: X\nbody:\n  - type: input\n    attributes:\n      label: A\n      required: true\n"
    assert form_state(text) == "none"


@pytest.mark.parametrize(("on", "met"), [
    ("on:\n  issues:\n    types: [opened]\n", True),
    ("on:\n  issues:\n    types:\n      - labeled\n      - opened\n", True),
    ("on:\n  issues:\n    types: opened\n", True),
    ("on:\n  issues:\n", True),  # no types: every activity type
    ("on: [issues]\n", True),
    ("on: issues\n", True),
    ("on:\n  issues: {types: [opened]}\n", True),
    ("on:\n  issues:\n    types: [labeled]\n", False),
    ("on: {issues: {types: [labeled]}}\n", False),
    ("on: {issues: {types: [opened, labeled]}}\n", True),
    ("on:\n  pull_request:\n", False),
])
def test_the_issues_opened_trigger(tmp_path: Path, on: str, met: bool) -> None:
    triage = _read(TRIAGE).replace("on:\n  issues:\n    types: [opened]\n", on)
    assert scan_backlog_ready(_repo(tmp_path, {TRIAGE: triage}))["untriaged_on_open"] is met


@pytest.mark.parametrize("step", [
    "      - uses: actions-ecosystem/action-add-labels@v1\n        with:\n          labels: needs-triage\n",
    "      - uses: actions/github-script@v7\n        with:\n          script: |\n"
    "            github.rest.issues.addLabels({owner, repo, issue_number, labels: ['status: needs triage']})\n",
    "      - run: gh issue edit 1 --add-label \"needs triage\"\n",
    "      - run: gh issue edit 1 --add-label bug,needs-triage\n",
    "      - run: gh issue create --title t --label needs-triage\n",
    "      - uses: actions/github-script@v7\n        with:\n          script: |\n"
    "            await github.rest.issues.addLabels({\n              owner, repo, issue_number,\n"
    "              labels: ['needs-triage'],\n            })\n",
    "      - uses: andymckay/labeler@1.0.4\n        with:\n          add-labels: \"needs-triage\"\n",
])
def test_label_adding_steps(tmp_path: Path, step: str) -> None:
    wf = "on:\n  issues:\n    types: [opened]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n" + step
    assert scan_backlog_ready(_repo(tmp_path, {TRIAGE: wf}))["untriaged_on_open"] is True


def test_label_and_call_must_share_a_step(tmp_path: Path) -> None:
    wf = ("on:\n  issues:\n    types: [opened]\njobs:\n  triage:\n    runs-on: ubuntu-latest\n    steps:\n"
          "      - run: echo needs-triage\n      - run: gh issue edit 1 --add-label bug\n")
    assert scan_backlog_ready(_repo(tmp_path, {TRIAGE: wf}))["untriaged_on_open"] is False


@pytest.mark.parametrize("indicator", [">-", "|"])
def test_a_block_scalar_exempt_list_is_read(indicator: str) -> None:
    from lib.backlog_ready import _with_inputs
    from lib.ci.ci_masking import IndentTree, parse_lines
    text = (f"jobs:\n  s:\n    steps:\n      - uses: actions/stale@v9\n        with:\n"
            f"          exempt-issue-labels: {indicator}\n            pinned,\n            agent-ready\n")
    tree = IndentTree(parse_lines(text.splitlines()))
    uses = next(i for i, ln in enumerate(tree.lines) if ln.key == "uses")
    assert stale_state(_with_inputs(tree, uses), READY_LABELS) == "safe"


@pytest.mark.parametrize("step", [
    "      - name: needs-triage --add-label\n        run: echo hi\n",  # a step name
    "      # gh issue edit --add-label needs-triage\n      - run: echo hi\n",  # a comment
    "      - if: contains(github.event.issue.labels.*.name, 'needs-triage')\n        run: gh issue edit --add-label bug\n",
    "      - run: echo needs-triage\n",  # names the label but adds none
    "      - run: gh issue edit --add-label needs-triage-later\n",  # another label
    "      - uses: actions-ecosystem/action-remove-labels@v1\n        with:\n          labels: needs-triage\n",
    "      - run: gh issue list --label needs-triage\n",  # lists, adds nothing
    "      - uses: andymckay/labeler@1.0.4\n        with:\n          remove-labels: needs-triage\n",
    "      - id: triage\n        run: gh issue edit 1 --add-label bug\n",  # a step id
    "      - run: gh issue edit 1 --add-label bug --body-file triage.md\n",  # a path
    "      - run: gh issue list --label triage | xargs -n1 gh issue edit --add-label bug\n",  # a filter
])
def test_lookalikes_are_not_an_untriaged_label(tmp_path: Path, step: str) -> None:
    wf = "on:\n  issues:\n    types: [opened]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n" + step
    assert scan_backlog_ready(_repo(tmp_path, {TRIAGE: wf}))["untriaged_on_open"] is False


def test_an_unreadable_on_key_reads_not_measured(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {TRIAGE: "on: [issues,\njobs:\n  x:\n    steps: []\n"}))
    assert (block["untriaged_on_open"], block["untriaged_on_open_reason"]) == (None, "workflow_unreadable")


def test_a_workflow_in_a_subfolder_is_not_read(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {TRIAGE: None, ".github/workflows/sub/triage.yml": _read(TRIAGE)}))
    assert block["untriaged_on_open"] is False


@pytest.mark.parametrize(("text", "met"), [
    ("## `agent-ready`\n\nSome words.\n\n- has criteria\n", True),
    ("## The `Agent-ready` label\n\n### Criteria\n\n1. has criteria\n", True),
    ("State `READY` then:\n\n- start\n", False),  # a constant, not the label
    ("A documented ready label is the bar:\n\n- one\n", False),  # prose
    ("Make your codebase agent-ready:\n\n- add tests\n", False),  # prose, not a label
    ("## Backlog kept agent-ready\n\n- one\n", False),
    ("Label an issue `ready` when:\n\n- it has criteria\n", True),
    ("Use the `ready` label when:\n* it has criteria\n", True),
    ("Set status to \"ready\" when done.\n\n- one\n", False),  # a quoted word, not the label
    ("Issues marked \"triage/accepted\" meet these:\n- one\n", True),
    ("## `refine::ready`\n\n- has success criteria\n", True),  # a GitLab scoped label
    ("## `refine::readying`\n\n- not the label\n", False),
    ("When you are ready to merge:\n\n- push\n", False),  # prose, no label
    ("The `agent-ready` label is applied by hand.\n\nOther text.\n\n- unrelated\n", False),
    ("## agent-ready\n\nNo list here.\n\n## Other\n\n- unrelated\n", False),
    ("```\n## agent-ready\n- in code\n```\n", False),
    ("```\n~~~\n## `agent-ready`\n- still in code\n```\n", False),  # a ~~~ inside ``` closes nothing
    ("- `agent-ready`: the agent-ready label\n", False),  # a list item naming it, no criteria
])
def test_ready_label_documentation(text: str, met: bool) -> None:
    assert documents_ready_label(text, READY_LABELS) is met


def test_a_fixture_or_assess_doc_is_not_documentation(tmp_path: Path) -> None:
    doc = _read(DOC)
    files: dict[str, str | None] = {DOC: None, ".assess/report.md": doc, "tests/fixtures/x/README.md": doc}
    assert scan_backlog_ready(_repo(tmp_path, files))["ready_label_documented"] is False
    assert scan_backlog_ready(_repo(tmp_path / "b", {DOC: None, "docs/triage.md": doc}))["ready_label_documented"] is True


# --- success criteria 2 and 3: the model row -----------------------------------------

def test_the_model_row_scores_the_fixtures(tmp_path: Path) -> None:
    model = _model()
    (row,) = model["rows"]
    assert row["id"] == "backlog-ready" and not model.get("steps")
    states = {}
    for name, files in (("met", {}), ("unmet", {TRIAGE: None}), ("unknown", {FORM: "body: [\n"})):
        block = scan_backlog_ready(_repo(tmp_path / name, files))
        states[name] = lt.level_result(model, {"backlog_ready": block})["conditions"][0]["state"]
    assert states == {"met": "met", "unmet": "unmet", "unknown": "not_measured"}


def test_level_1_is_listed_not_counted(tmp_path: Path) -> None:
    for c in _model()["rows"][0]["levels"][0]["criteria"]:
        assert criterion_grade(c) == "config_only"
    model = lt.default_model()
    met = scan_backlog_ready(_repo(tmp_path))
    before = lt.level_result(model, {})
    after = lt.level_result(model, {"backlog_ready": met})
    assert before["tally"]["met"] == after["tally"]["met"]  # N unchanged
    assert before["tally"]["measured"] == after["tally"]["measured"]
    assert CID in after["listed"] and CID not in after["counted"]
    assert before["step"] == after["step"]


def test_the_row_is_a_default_practice_and_no_gate() -> None:
    # Success criterion 2 of #1324: exactly one shipped model holds the row, and no Step gates it.
    assert "backlog_ready" not in STAGED_BLOCKS
    models = [DEFAULT_LEVELS, *sorted((DEFAULT_LEVELS.parent / "profiles").glob("*.toml"))]
    holding = [p.name for p in models
               if any(r["id"] == "backlog-ready" for r in tomllib.loads(p.read_text(encoding="utf-8"))["rows"])]
    assert holding == ["default.toml"]
    assert "backlog-ready" not in str(tomllib.loads(DEFAULT_LEVELS.read_text(encoding="utf-8")).get("steps", []))
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("backlog-ready.")]


# --- #1324 success criterion 1: discrimination on the #842 corpus ----------------------

L2 = "backlog-ready.2"


def _corpus_split(corpus_dir: Path) -> dict[str, int]:
    """How ``backlog-ready.2`` falls across the corpus expected-state files, counts only."""
    expected = cc.load_corpus(corpus_dir)
    states = {e["repo"]: e["states"] for e in expected}
    missing = [e["repo"] for e in expected if L2 not in e["states"]]
    assert not missing, (f"{len(missing)} expected-state file(s) predate the backlog-ready row: reseed with "
                         "scripts/calibration_corpus.py seed CLONE_ROOT --platform")
    return cc.row_split(states, "backlog-ready").get(L2, {})


def test_corpus_discriminates() -> None:
    """At least one corpus repository meets level 2 and at least one does not (#1324, #1318 criterion 7).

    The expected-state files are seeded by hand from the pinned clones with
    ``scripts/calibration_corpus.py seed CLONE_ROOT --platform``; until they are, there is
    nothing to read and the test skips, saying so."""
    if not cc.load_corpus(cc.CORPUS_DIR):
        pytest.skip(f"no corpus expected-state files under {cc.CORPUS_DIR.name}/ yet: seed them with "
                    "scripts/calibration_corpus.py seed CLONE_ROOT --platform")
    split = _corpus_split(cc.CORPUS_DIR)
    assert split["met"] >= 1 and split["unmet"] >= 1, split


def test_the_corpus_check_runs_once_corpus_files_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#1628: ``test_corpus_discriminates`` skips only while no corpus file exists; a corpus whose
    files lack the backlog-ready row fails it, and one that discriminates passes."""
    monkeypatch.setattr(cc, "CORPUS_DIR", tmp_path / "corpus")
    with pytest.raises(pytest.skip.Exception, match="no corpus expected-state files"):
        test_corpus_discriminates()
    _write_corpus(tmp_path / "corpus", [None, None])
    with pytest.raises(AssertionError, match="predate the backlog-ready row"):
        test_corpus_discriminates()
    _write_corpus(tmp_path / "corpus", ["met", "not_measured"])
    with pytest.raises(AssertionError):
        test_corpus_discriminates()
    _write_corpus(tmp_path / "corpus", ["met", "unmet"])
    test_corpus_discriminates()


def _write_corpus(corpus_dir: Path, states: list[str | None]) -> None:
    for n, state in enumerate(states):
        cc.write_expected(corpus_dir, {"corpus_version": cc.CORPUS_VERSION, "repo": f"h:{n:016x}",
                                       "pinned": f"h:{n + 100:016x}", "levels_version": "v",
                                       "states": {} if state is None else {"backlog-ready.1": "met", L2: state}})


def test_the_corpus_check_counts_met_and_unmet_apart(tmp_path: Path) -> None:
    _write_corpus(tmp_path / "split", ["met", "unmet", "not_measured"])
    split = _corpus_split(tmp_path / "split")
    assert (split["met"], split["unmet"], split["not_measured"]) == (1, 1, 1)
    # Met nowhere measured: not_measured is not unmet, so the corpus does not discriminate.
    _write_corpus(tmp_path / "flat", ["met", "not_measured"])
    assert _corpus_split(tmp_path / "flat")["unmet"] == 0
    _write_corpus(tmp_path / "old", ["met", None])
    with pytest.raises(AssertionError, match="predate the backlog-ready row"):
        _corpus_split(tmp_path / "old")


def test_the_manifest_signals_are_config_text() -> None:
    signals = {e["path"]: e for e in cm.core_manifest()["entries"] if e.get("kind") == "signal"
               and str(e.get("path", "")).startswith("backlog_ready.")}
    level1 = {p for p, s in signals.items() if s["evidence"] == "config_text"}  # type: ignore[typeddict-item]
    assert sorted(level1) == sorted(f"backlog_ready.{s}" for s in SIGNALS)
    # Level 2 (#1323) reads the tracker: every other backlog_ready signal is platform evidence.
    assert {s["evidence"] for p, s in signals.items() if p not in level1} == {"platform"}  # type: ignore[typeddict-item]


# --- counts-only ----------------------------------------------------------------------

def test_counts_only_keeps_every_field(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {FORM: "body: [\n"}))
    out = counts_only({"backlog_ready": block})["backlog_ready"]
    for field in ("available", *SIGNALS, "required_form_field_reason", "forms", "forms_unreadable",
                  "issue_templates", "workflows", "stale_jobs", "labels_from_config"):
        assert out[field] == block[field], field  # type: ignore[literal-required]
