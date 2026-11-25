"""The adoption examples in README.md and docs/refine.md call what really exists (issue #1327).

Every `uses: bjcoombs/ai-native-toolkit/.github/workflows/<file>@<ref>` line
in a fenced YAML block must name a reusable workflow on disk, pass only
inputs and secrets its `workflow_call` declares (and every required one),
and pin the tag of the current plugin version. Every `include: remote:` line
that fetches a toolkit template must name a template on disk, pass only
inputs its `spec:inputs` header declares, and pin the same tag; the refine
page's GitLab section includes `templates/refine.yml`. The release
PR moves the pins with `.claude-plugin/plugin.json`
(`docs/contributing/release.md`).
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
DOCS = [REPO / "README.md", REPO / "docs" / "refine.md"]
WORKFLOW_PREFIX = "bjcoombs/ai-native-toolkit/.github/workflows/"
REMOTE_RE = re.compile(r"^https://raw\.githubusercontent\.com/bjcoombs/ai-native-toolkit/([^/]+)/(.+)$")
FENCE_RE = re.compile(r"^```ya?ml\n(.*?)^```", re.MULTILINE | re.DOTALL)
REFINE_CALLERS = {"reusable-refine-triage.yml": "refine-triage.yml",
                  "reusable-refine-dedupe.yml": "refine-dedupe.yml",
                  "reusable-refine-sweep.yml": "refine-sweep.yml"}


def _tag() -> str:
    return "v" + json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def _yaml_blocks(doc: Path) -> Iterator[Any]:
    for block in FENCE_RE.findall(doc.read_text(encoding="utf-8")):
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            data["on"] = data.pop(True, data.get("on"))  # PyYAML reads the bare key `on` as True
        yield data


def _workflow_call(name: str) -> Dict[str, Any]:
    doc = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    call: Dict[str, Any] = doc[True]["workflow_call"]
    return call


def _uses(doc: Path) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(caller document, job) for each job that calls a toolkit workflow."""
    out = []
    for data in _yaml_blocks(doc):
        if isinstance(data, dict):
            for job in (data.get("jobs") or {}).values():
                if str(job.get("uses", "")).startswith(WORKFLOW_PREFIX):
                    out.append((data, job))
    return out


def _includes(doc: Path) -> List[Dict[str, Any]]:
    out = []
    for data in _yaml_blocks(doc):
        if isinstance(data, dict):
            for inc in data.get("include") or []:
                if isinstance(inc, dict) and REMOTE_RE.match(str(inc.get("remote", ""))):
                    out.append(inc)
    return out


def test_the_docs_carry_the_examples() -> None:
    readme, page = DOCS
    assert {j["uses"].split("@")[0].rsplit("/", 1)[1] for _, j in _uses(page)} == set(REFINE_CALLERS)
    assert any("reusable-refine-" in j["uses"] for _, j in _uses(readme))
    assert _includes(readme), "README.md's GitLab section has an include: remote: line"
    assert [REMOTE_RE.match(i["remote"]).group(2) for i in _includes(page)] == ["templates/refine.yml"], \
        "docs/refine.md's GitLab section includes the refiner template (#1327)"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_uses_lines_name_real_workflows_inputs_and_secrets(doc: Path) -> None:
    for _, job in _uses(doc):
        path, _, ref = job["uses"][len(WORKFLOW_PREFIX):].partition("@")
        assert (REPO / ".github" / "workflows" / path).is_file(), job["uses"]
        assert ref == _tag(), f"{job['uses']}: pin the current release tag {_tag()}"
        call = _workflow_call(path)
        inputs = call.get("inputs") or {}
        assert set(job.get("with") or {}) <= set(inputs), (job["uses"], set(job["with"]) - set(inputs))
        required = {k for k, v in inputs.items() if v.get("required")}
        assert required <= set(job.get("with") or {}), (job["uses"], required)
        assert set(job.get("secrets") or {}) <= set(call.get("secrets") or {}), job["uses"]
        assert "vars.REFINE_ENABLED == 'true'" in job["if"], "the example keeps the kill switch"
        assert job["with"]["mode"] == "${{ vars.REFINE_MODE || 'dry-run' }}", "the example defaults to dry-run"


def test_example_callers_grant_what_this_repository_grants() -> None:
    for caller, job in _uses(DOCS[1]):
        reusable = job["uses"][len(WORKFLOW_PREFIX):].partition("@")[0]
        ours = yaml.safe_load((REPO / ".github" / "workflows" / REFINE_CALLERS[reusable]).read_text(encoding="utf-8"))
        assert caller["permissions"] == ours["permissions"], reusable
        assert set(caller["on"]) <= set(ours[True]), reusable


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_include_lines_name_real_templates_and_inputs(doc: Path) -> None:
    for inc in _includes(doc):
        m = REMOTE_RE.match(inc["remote"])
        assert m
        ref, path = m.groups()
        template = REPO / path
        assert template.is_file(), inc["remote"]
        assert ref == _tag(), f"{inc['remote']}: pin the current release tag {_tag()}"
        spec = next(yaml.safe_load_all(template.read_text(encoding="utf-8")))["spec"]["inputs"]
        assert set(inc.get("inputs") or {}) <= set(spec), (inc["remote"], set(inc["inputs"]) - set(spec))


def test_settings_table_names_real_inputs() -> None:
    text = DOCS[1].read_text(encoding="utf-8")
    section = text.split("## Settings", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"^\| `([a-z_]+)`(?:, `([a-z_]+)`)?", section, re.MULTILINE))
    names = {n for pair in named for n in pair if n}
    assert names, "the Settings table lists inputs"
    declared = set().union(*(set(_workflow_call(n).get("inputs") or {}) for n in REFINE_CALLERS))
    assert names <= declared, names - declared


def test_model_examples_pass_the_claude_code_oauth_token() -> None:
    for _, job in _uses(DOCS[1]):
        reusable = job["uses"][len(WORKFLOW_PREFIX):].partition("@")[0]
        if "claude_code_oauth_token" in (_workflow_call(reusable).get("secrets") or {}):
            assert job["secrets"]["claude_code_oauth_token"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}", reusable


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_docs_state_what_the_oauth_token_costs(doc: Path) -> None:
    text = " ".join(doc.read_text(encoding="utf-8").split())
    for claim in ("`CLAUDE_CODE_OAUTH_TOKEN`", "simplest", "usage limits", "no spend cap",
                  "own sessions", "recommended", "`REFINE_ANTHROPIC_API_KEY`", "spend limit"):
        assert claim in text, (doc.name, claim)
