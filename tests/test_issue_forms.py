"""Issue intake: the forms, the definition of ready and the needs-triage workflow (#1319).

Issues filed through `gh issue create --body` or the REST API bypass the forms, so
the definition of ready in `docs/contributing/definition-of-ready.md` is the source
of truth. Its one fenced `yaml` block is what the refinement script (#1320) will read,
so these tests pin that block's shape, the forms that ask humans for the same
sections, and the workflow that marks every new issue as untriaged.
"""
import re
from pathlib import Path
from typing import Any

import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / ".github" / "ISSUE_TEMPLATE"
DOR = REPO / "docs" / "contributing" / "definition-of-ready.md"
WORKFLOW = REPO / ".github" / "workflows" / "issue-needs-triage.yml"

SECTIONS = [
    "Problem Statement",
    "Technical Context",
    "Impact",
    "Root Cause",
    "Solution Requirements",
    "Success Criteria",
]
FENCE_RE = re.compile(r"^```yaml\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def _forms() -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(p for p in TEMPLATES.glob("*.yml") if p.name != "config.yml")
    return [(p, yaml.safe_load(p.read_text(encoding="utf-8"))) for p in paths]


def _required_labels(form: dict[str, Any]) -> set[str]:
    return {
        field["attributes"]["label"]
        for field in form.get("body", [])
        if field.get("type") == "textarea"
        and field.get("validations", {}).get("required") is True
    }


def _ready_block() -> dict[str, Any]:
    blocks = FENCE_RE.findall(DOR.read_text(encoding="utf-8"))
    assert len(blocks) == 1, "the definition of ready holds exactly one fenced yaml block"
    block = yaml.safe_load(blocks[0])
    assert isinstance(block, dict)
    return block


def _workflow() -> tuple[str, dict[str, Any]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    # PyYAML reads the bare key `on` as the boolean True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return text, doc


def test_every_form_parses_with_a_name_description_and_body() -> None:
    forms = _forms()
    assert forms, "at least one issue form under .github/ISSUE_TEMPLATE/"
    for path, form in forms:
        assert isinstance(form, dict), path.name
        for key in ("name", "description", "body"):
            assert form.get(key), f"{path.name} lacks {key}"
        ids = [f["id"] for f in form["body"] if "id" in f]
        assert len(ids) == len(set(ids)), f"{path.name} repeats a field id"


def test_a_form_requires_problem_statement_and_success_criteria() -> None:
    assert any(
        {"Problem Statement", "Success Criteria"} <= _required_labels(form)
        for _, form in _forms()
    )


def test_form_field_labels_are_the_ready_section_headings() -> None:
    """A form issue renders each label as a heading the script looks for."""
    sections = set(_ready_block()["sections"])
    for path, form in _forms():
        labels = {
            f["attributes"]["label"] for f in form["body"] if f.get("type") == "textarea"
        }
        assert labels <= sections, f"{path.name}: {sorted(labels - sections)}"


def test_blank_issues_are_disabled() -> None:
    config = yaml.safe_load((TEMPLATES / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False


def test_ready_block_lists_the_sections_and_labels() -> None:
    block = _ready_block()
    assert block["sections"] == SECTIONS
    assert block["labels"] == {"triage": "needs-triage", "ready": "agent-ready"}
    # GitHub fills an empty optional form field with this text.
    assert "_No response_" in block["empty_markers"]


def test_ready_block_headings_match_the_document_sections() -> None:
    """Every checklist id in the block has a `### <id>` entry in the prose."""
    text = DOR.read_text(encoding="utf-8")
    checklist = _ready_block()["checklist"]
    assert checklist
    for item in checklist:
        assert item["check"] in {"script", "judgement"}, item
        assert re.search(rf"^### `{re.escape(item['id'])}`", text, re.MULTILINE), item


def test_workflow_triggers_on_issue_opened_only() -> None:
    _, doc = _workflow()
    assert doc["on"] == {"issues": {"types": ["opened"]}}


def test_workflow_has_minimum_permissions() -> None:
    _, doc = _workflow()
    assert doc["permissions"] == {"issues": "write"}
    for job in doc["jobs"].values():
        assert "permissions" not in job


def test_workflow_uses_only_the_workflow_token() -> None:
    text, _ = _workflow()
    assert set(re.findall(r"secrets\.(\w+)", text)) <= {"GITHUB_TOKEN"}


def test_workflow_has_no_checkout_and_no_action() -> None:
    _, doc = _workflow()
    for job in doc["jobs"].values():
        for step in job["steps"]:
            assert "uses" not in step, step


def test_workflow_applies_the_triage_label_named_in_the_block() -> None:
    text, _ = _workflow()
    labels = _ready_block()["labels"]
    assert f"--add-label {labels['triage']}" in text
    # An issue already carrying the ready label when the job runs is skipped.
    assert f'index("{labels["ready"]}")' in text
