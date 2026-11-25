"""lib/backlog_ready.py and the backlog-ready level 1 condition (#1322): intake configuration at HEAD."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

import lib.level_tally as lt
import lib.manifest.check_manifest as cm
from lib.assess_config import load_backlog_ready_labels
from lib.backlog_ready import (READY_LABELS, UNTRIAGED_LABELS, documents_ready_label, form_state,
                               scan_backlog_ready, stale_state)
from lib.counts_only.content_free import counts_only
from lib.level_signals import STAGED_BLOCKS, criterion_grade, signal_path_validator
from lib.levels import MaturityModel, load_levels

FIXTURES = Path(__file__).parent / "fixtures" / "backlog_ready"
MODEL = FIXTURES / "levels.toml"
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
    return load_levels(MODEL, signal_path_validator())


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

def test_a_gitlab_repository_reads_the_form_not_measured(tmp_path: Path) -> None:
    root = tmp_path / "gl"
    (root / ".gitlab" / "issue_templates").mkdir(parents=True)
    (root / ".gitlab" / "issue_templates" / "Bug.md").write_text("## Summary\n", encoding="utf-8")
    (root / ".gitlab-ci.yml").write_text("test:\n  script: [make test]\n", encoding="utf-8")
    block = scan_backlog_ready(root)
    assert (block["required_form_field"], block["required_form_field_reason"]) == (None, "gitlab_templates")
    assert (block["untriaged_on_open"], block["untriaged_on_open_reason"]) == (None, "gitlab_ci_not_read")
    assert _condition(block)["state"] == "unmet"  # no ready label documented
    (root / "CONTRIBUTING.md").write_text("## `refine::ready`\n\n- has success criteria\n", encoding="utf-8")
    block = scan_backlog_ready(root)
    assert block["ready_label_documented"] is True
    assert _condition(block)["state"] == "not_measured"  # never unmet for want of a GitHub file


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
    from lib.ci_masking import IndentTree, parse_lines
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


# --- success criteria 2 and 3: the test model -----------------------------------------

def test_the_test_model_loads_and_scores_the_fixtures(tmp_path: Path) -> None:
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
    combined = lt.default_model()
    combined["rows"] = [*combined["rows"], *_model()["rows"]]
    met = scan_backlog_ready(_repo(tmp_path))
    before = lt.level_result(combined, {})
    after = lt.level_result(combined, {"backlog_ready": met})
    assert before["tally"]["met"] == after["tally"]["met"]  # N unchanged
    assert before["tally"]["measured"] == after["tally"]["measured"]
    assert CID in after["listed"] and CID not in after["counted"]
    assert before["step"] == after["step"]


def test_the_row_is_staged_outside_the_default_model() -> None:
    assert STAGED_BLOCKS["backlog_ready"] == 1324
    assert not [r for r in lt.default_model()["rows"] if r["id"] == "backlog-ready"]
    assert not [c for c in lt.gate_conditions(lt.default_model()) if c.startswith("backlog-ready.")]


def test_the_manifest_signals_are_config_text() -> None:
    signals = {e["path"]: e for e in cm.core_manifest()["entries"] if e.get("kind") == "signal"
               and str(e.get("path", "")).startswith("backlog_ready.")}
    assert sorted(signals) == sorted(f"backlog_ready.{s}" for s in SIGNALS)
    assert {s["evidence"] for s in signals.values()} == {"config_text"}  # type: ignore[typeddict-item]


# --- counts-only ----------------------------------------------------------------------

def test_counts_only_keeps_every_field(tmp_path: Path) -> None:
    block = scan_backlog_ready(_repo(tmp_path, {FORM: "body: [\n"}))
    out = counts_only({"backlog_ready": block})["backlog_ready"]
    for field in ("available", *SIGNALS, "required_form_field_reason", "forms", "forms_unreadable",
                  "workflows", "stale_jobs", "labels_from_config"):
        assert out[field] == block[field], field  # type: ignore[literal-required]
