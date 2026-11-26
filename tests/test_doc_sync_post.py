"""The doc-sync posting script and workflow guardrails (issue #1453).

`skills/doc-contradictions/scripts/doc_sync_post.py` is the only code in
`.github/workflows/doc-sync.yml` that holds a write token; it runs here
against a recorded `gh api` with no network. The workflow tests pin the
guardrails: the skill comes from the base commit, the model job's scopes are
read-only, only the post job writes, and the comparers run on Haiku.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "doc-contradictions" / "scripts"
WORKFLOW = REPO / ".github" / "workflows" / "doc-sync.yml"
AGENT = REPO / "agents" / "doc-contradictions-comparer.md"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import doc_sync_post  # noqa: E402

HEAD = "a" * 40
BOT = {"login": "github-actions[bot]"}
PATCH = "@@ -1,3 +1,4 @@\n line one\n-old\n+new\n+added\n line four\n@@ -20,2 +21,2 @@\n ctx\n-x\n+y\n"


def finding(fid: str, a_path: str = "docs/caching.md", a_line: int = 9) -> Dict[str, Any]:
    return {"id": fid, "kind": "doc-vs-code", "subject": "cache expiry", "confidence": "high",
            "a": {"path": a_path, "line": a_line, "quote": "Entries expire after 600 seconds"},
            "b": {"path": "src/cache.py", "line": 4, "quote": "DEFAULT_TTL_SECONDS = 300"},
            "why": "600 against 300.", "cluster": "c01"}


class FakeGh:
    def __init__(self, files: List[Dict[str, Any]], review: List[Dict[str, Any]],
                 issue: List[Dict[str, Any]]) -> None:
        self.reads = {"files": files, "pulls/7/comments": review, "issues/7/comments": issue}
        self.writes: List[Tuple[List[str], Dict[str, Any]]] = []

    def __call__(self, args: Sequence[str], payload: Optional[str]) -> str:
        if args[0] == "--paginate":
            for key, value in self.reads.items():
                if args[1].endswith(key):
                    return json.dumps(value)
            raise AssertionError(args)
        self.writes.append((list(args), json.loads(payload or "{}")))
        return "{}"


def run(plan: Dict[str, Any], gh: FakeGh) -> List[Dict[str, Any]]:
    return doc_sync_post.post(plan, "acme/widgets", 7, HEAD, run=gh)


def checked(threads: List[Dict[str, Any]], summary: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"status": "checked", "clusters": 1, "cross_cluster": False, "threads": threads,
            "summary": summary or [], "dropped": []}


def test_diff_lines_reads_new_side_lines() -> None:
    assert doc_sync_post.diff_lines(PATCH) == {1, 2, 3, 4, 21, 22}


def test_thread_sits_on_the_cited_line_when_it_is_in_the_diff() -> None:
    gh = FakeGh([{"filename": "src/cache.py", "patch": PATCH}], [], [])
    posted = run(checked([finding("0" * 16)]), gh)
    assert posted[0]["path"] == "src/cache.py" and posted[0]["line"] == 4 and posted[0]["side"] == "RIGHT"
    assert "<!-- doc-sync:finding:" + "0" * 16 + " -->" in posted[0]["body"]
    assert posted[0]["commit_id"] == HEAD
    assert [w[0][2] for w in gh.writes] == ["repos/acme/widgets/pulls/7/comments",
                                            "repos/acme/widgets/issues/7/comments"]


def test_thread_falls_back_to_a_file_comment() -> None:
    gh = FakeGh([{"filename": "docs/caching.md", "patch": PATCH}], [], [])
    posted = run(checked([finding("1" * 16)]), gh)
    assert posted[0]["subject_type"] == "file" and "line" not in posted[0]


def test_existing_thread_is_not_posted_twice_and_summary_is_edited() -> None:
    marker = "<!-- doc-sync:finding:" + "2" * 16 + " -->"
    gh = FakeGh([{"filename": "src/cache.py", "patch": PATCH}], [{"body": "x\n" + marker, "user": BOT}],
                [{"id": 55, "body": doc_sync_post.SUMMARY_MARKER + "\nold", "user": BOT}])
    assert run(checked([finding("2" * 16)]), gh) == []
    (args, body), = gh.writes
    assert args[:3] == ["--method", "PATCH", "repos/acme/widgets/issues/comments/55"]
    assert "1 already posted" in body["body"]


def test_markers_in_comments_by_others_are_ignored() -> None:
    marker = "<!-- doc-sync:finding:" + "4" * 16 + " -->"
    someone = {"login": "someone"}
    gh = FakeGh([{"filename": "src/cache.py", "patch": PATCH}], [{"body": marker, "user": someone}],
                [{"id": 77, "body": doc_sync_post.SUMMARY_MARKER, "user": someone}])
    assert len(run(checked([finding("4" * 16)]), gh)) == 1
    assert [w[0][1] for w in gh.writes] == ["POST", "POST"]


def test_quotes_sit_in_code_spans() -> None:
    f = finding("5" * 16)
    f["a"]["quote"] = "Maintainer: @alice `x`"
    body = doc_sync_post.thread_body(f)
    assert "`docs/caching.md:9`: `Maintainer: @alice 'x'`" in body


def test_quiet_run_creates_no_comment_but_updates_a_stale_one() -> None:
    quiet = {"status": "skipped", "reason": "no document changed", "quiet": True, "threads": [], "summary": []}
    gh = FakeGh([], [], [])
    run(dict(quiet), gh)
    assert gh.writes == []
    gh = FakeGh([], [], [{"id": 9, "body": doc_sync_post.SUMMARY_MARKER, "user": BOT}])
    run(dict(quiet), gh)
    assert "Not run: no document changed." in gh.writes[0][1]["body"]
    gh = FakeGh([], [], [])
    run(checked([]), gh)
    assert gh.writes == []


@pytest.mark.parametrize("plan,text", [
    ({"status": "skipped", "reason": "over the limit of 40; run /doc-contradictions by hand",
      "threads": [], "summary": []}, "Not run: over the limit of 40"),
    ({"status": "error", "reason": "the model step returned no findings document", "clusters": 2,
      "threads": [], "summary": []}, "The check did not complete"),
])
def test_a_run_that_could_not_check_always_writes_the_summary(plan: Dict[str, Any], text: str) -> None:
    gh = FakeGh([], [], [])
    run(plan, gh)
    (args, body), = gh.writes
    assert args[2] == "repos/acme/widgets/issues/7/comments" and text in body["body"]


def test_summary_lists_non_blocking_findings() -> None:
    gh = FakeGh([], [], [])
    low = dict(finding("3" * 16), confidence="low")
    run(checked([], [low]), gh)
    (args, body), = gh.writes
    assert args[2] == "repos/acme/widgets/issues/7/comments"
    assert "Other findings (not blocking)" in body["body"] and "`src/cache.py:4`" in body["body"]


def test_head_sha_must_be_full(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        doc_sync_post.main(["--plan", str(plan), "--repo", "a/b", "--pr", "1", "--head-sha", "main"])


# --- workflow guardrails ----------------------------------------------------

def _workflow() -> Dict[str, Any]:
    doc: Dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc


def _steps(job: str) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = _workflow()["jobs"][job]["steps"]
    return steps


def test_workflow_runs_on_pull_requests_and_is_not_required() -> None:
    doc = _workflow()
    assert list(doc[True]["pull_request"]["branches"]) == ["main"]
    assert "merge_group" not in doc[True]
    assert doc["permissions"] == {"contents": "read"}
    ci = (REPO / "docs" / "contributing" / "ci.md").read_text(encoding="utf-8")
    assert "doc-sync.yml" in ci


def test_skill_and_scripts_come_from_the_base_commit() -> None:
    base = "${{ github.event.pull_request.base.sha }}"
    for job in ("check", "post"):
        checkouts = [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert all(s["with"]["persist-credentials"] is False for s in checkouts)
        trusted = [s for s in checkouts if s["with"].get("path") != "pr-head"]
        assert trusted and all(s["with"]["ref"] == base for s in trusted)
        assert all(s["with"]["repository"] == "${{ github.repository }}" for s in trusted)
    head = [s for s in _steps("check") if s.get("with", {}).get("path") == "pr-head"]
    assert len(head) == 1
    for step in _steps("check") + _steps("post"):
        run = str(step.get("run", ""))
        assert "pr-head/skills" not in run and "python3 pr-head" not in run


def test_model_job_is_read_only_and_only_post_writes() -> None:
    jobs = _workflow()["jobs"]
    check = jobs["check"]["permissions"]
    assert {k: v for k, v in check.items() if v == "write"} == {"id-token": "write"}
    assert jobs["post"]["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in _steps("post"))
    model = [s for s in _steps("check") if "claude-code-action" in str(s.get("uses", ""))]
    assert len(model) == 1
    args = model[0]["with"]["claude_args"]
    for tool in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert tool in args.split("--disallowedTools")[1]
    denied = args.split("--disallowedTools")[1].split("\n")[0]
    for secret in ("Read(//proc/**)", "Read(//home/runner/work/_temp/**)", "Read(//tmp/**)", "Read(~/.*/**)"):
        assert secret in denied
    allowed = args.split("--allowedTools")[1].split("\n")[0]
    assert "Bash" not in allowed and "Write" not in allowed
    assert model[0]["uses"].split("@")[1].split()[0].isalnum() and len(model[0]["uses"].split("@")[1]) >= 40


def test_comparers_run_on_haiku() -> None:
    job = _workflow()["jobs"]["check"]
    assert job["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "haiku"
    model = [s for s in job["steps"] if "claude-code-action" in str(s.get("uses", ""))][0]
    assert 'model "haiku"' in model["with"]["prompt"]
    front = AGENT.read_text(encoding="utf-8").split("---")[1]
    assert yaml.safe_load(front)["model"] == "haiku"


def test_model_runs_only_for_trusted_same_repo_authors_with_the_refiner_credential_order() -> None:
    job = _workflow()["jobs"]["check"]
    assert "head.repo.full_name == github.repository" in job["if"]
    gate = [s for s in job["steps"] if s.get("id") == "gate"][0]
    assert "OWNER" in gate["env"]["TRUSTED"]
    script = gate["run"]
    assert script.index("api_key") < script.index("auth=wif") < script.index("auth=oauth")


def test_assess_record_is_excluded_from_the_per_pr_scope() -> None:
    scope = [s for s in _steps("check") if s.get("id") == "scope"][0]
    assert "--changed-from \"$BASE_SHA\"" in scope["run"] and "--exclude '.assess/**'" in scope["run"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="the gate step builds the schema with jq")
def test_findings_schema_is_safe_inside_single_quotes() -> None:
    """The gate step passes the schema as --json-schema '<schema>' (issue #1475).

    The value must hold no ' and no $schema key: Claude Code rejects the
    draft 2020-12 meta-schema URI as unknown.
    """
    model = [s for s in _steps("check") if "claude-code-action" in str(s.get("uses", ""))]
    assert "--json-schema '${{ steps.gate.outputs.schema }}'" in model[0]["with"]["claude_args"]
    gate = [s for s in _steps("check") if s.get("id") == "gate"][0]["run"]
    line = [ln for ln in gate.splitlines() if ln.strip().startswith('echo "schema=')][0]
    jq_filter = line.split("jq -c ", 1)[1].split(" ", 1)[0].strip("'")
    schema = SCRIPTS / "doc-sync-findings.schema.json"
    out = subprocess.run(["jq", "-c", jq_filter, str(schema)], capture_output=True,
                         encoding="utf-8", errors="replace", check=True).stdout
    assert "'" not in out
    assert "$schema" not in json.loads(out) and json.loads(out)["$defs"]["side"]
