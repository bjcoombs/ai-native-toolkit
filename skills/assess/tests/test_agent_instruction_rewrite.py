"""Tests for the agent instruction-rewrite hazard (#858, ``lib.agents.agent_instruction_rewrite``).

An agent job that can write files, while an instruction file or its own workflow has no
CODEOWNERS owner, holds the ``ci_runs_tests`` Step gate and never moves the tally. Kept apart
from ``test_agent_workflows.py``, which sits at the file-size limit.
"""
from __future__ import annotations

from pathlib import Path

import lib.scoring.level_tally as lt
from lib.agents.agent_instruction_rewrite import gh_aw_imports
from lib.agents.agent_ops import scan_agent_ops
from lib.counts_only.content_free import counts_only
from lib.docs.ownership_parser import codeowned

WORKFLOW = ".github/workflows/docs-sync.yml"

SCHEDULED_WRITER = """on:
  schedule:
    - cron: "0 3 * * 1"
jobs:
  sync:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - run: claude -p "Update AGENTS.md to match the code" && git commit -am sync && git push
"""

GH_AW_PR = """---
on:
  schedule: weekly on monday
permissions:
  contents: read
engine: claude
safe-outputs:
  create-pull-request:
---
# Refresh the agent guide
"""

OWNERS = "* @example-org/maintainers\n"
FILES_OWNED = ("**/AGENTS.md @example-org/maintainers\n/CLAUDE.md @example-org/maintainers\n"
               f"/{WORKFLOW} @example-org/maintainers\n/.github/CODEOWNERS @example-org/maintainers\n")


def _repo(git_repo, files: dict[str, str]) -> Path:
    repo, commit = git_repo
    for rel, text in {"AGENTS.md": "# Agents\n", **files}.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    commit("init")
    return repo


def _level(block: dict[str, object]) -> lt.LevelResult:
    data = {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"enforced_check_runs": True},
            "agent_ops": block}
    return lt.level_result(lt.default_model(), data)


def test_a_scheduled_writer_with_no_codeowners_holds_the_ci_gate_and_leaves_the_tally(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {WORKFLOW: SCHEDULED_WRITER}))
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_files"] == [WORKFLOW]
    held = _level(dict(block))  # type: ignore[arg-type]
    clean = _level({**block, "summary": {**block["summary"], "agent_instruction_rewrite": 0}})
    assert clean["step"] == 3 and held["step"] == 2
    assert held["tally"] == clean["tally"]
    assert held["next_step"] is not None
    assert {"kind": "gate", "gate": "ci_runs_tests", "condition": "agent-rewrites-instructions.1",
            "state": "met", "polarity": "hazard"} in held["next_step"]["blockers"]
    entry = next(h for h in held["hazards"] if h["id"] == "agent-rewrites-instructions.1")
    assert entry["gate"] == "ci_runs_tests" and "CODEOWNERS" in entry["fix"]


def test_codeowners_on_the_instruction_files_and_the_workflow_clears_it(git_repo) -> None:
    for owners in (OWNERS, FILES_OWNED):
        repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, ".github/CODEOWNERS": owners})
        summary = scan_agent_ops(repo)["summary"]
        assert summary["agent_instruction_rewrite"] == 0
        assert "agent_instruction_rewrite_reason" not in summary


def test_codeowners_that_leaves_the_workflow_unowned_still_raises_it(git_repo) -> None:
    owners = "/AGENTS.md @example-org/maintainers\n/CLAUDE.md @example-org/maintainers\n"
    repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, "CODEOWNERS": owners})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1


def test_a_read_only_agent_job_does_not_raise_it(git_repo) -> None:
    text = SCHEDULED_WRITER.replace("contents: write", "contents: read")
    summary = scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]
    assert summary["agent_instruction_rewrite"] == 0


def test_a_write_scope_without_contents_does_not_raise_it(git_repo) -> None:
    text = SCHEDULED_WRITER.replace("contents: write", "contents: read\n      issues: write")
    assert scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]["agent_instruction_rewrite"] == 0


def test_a_gh_aw_source_that_opens_pull_requests_raises_it_until_owned(git_repo) -> None:
    rel = ".github/workflows/guide.md"
    repo = _repo(git_repo, {rel: GH_AW_PR})
    block = scan_agent_ops(repo)
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_files"] == [rel]
    comment_only = GH_AW_PR.replace("create-pull-request:", "add-comment:")
    assert scan_agent_ops(_repo(git_repo, {rel: comment_only}))["summary"]["agent_instruction_rewrite"] == 0


def test_no_agent_job_or_an_unread_scope_is_not_measured(git_repo) -> None:
    summary = scan_agent_ops(_repo(git_repo, {}))["summary"]
    assert summary["agent_instruction_rewrite"] is None
    assert summary["agent_instruction_rewrite_reason"] == "no_agent_jobs"
    text = SCHEDULED_WRITER.replace("      contents: write\n", "      contents: ${{ inputs.scope }}\n")
    summary = scan_agent_ops(_repo(git_repo, {WORKFLOW: text}))["summary"]
    assert summary["agent_instruction_rewrite"] is None
    assert summary["agent_instruction_rewrite_reason"] == "agent_job_scope_unread"


def test_a_not_measured_hazard_holds_no_gate(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {}))
    assert _level(dict(block))["step"] == 3  # type: ignore[arg-type]


def test_the_count_and_files_survive_counts_only(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {WORKFLOW: SCHEDULED_WRITER}))
    kept = counts_only({"agent_ops": block})["agent_ops"]
    assert kept["summary"]["agent_instruction_rewrite"] == 1
    assert kept["agent_instruction_rewrite_files"] == [WORKFLOW]
    null = {**block, "summary": {**block["summary"], "agent_instruction_rewrite": None,
                                 "agent_instruction_rewrite_reason": "no_agent_jobs"}}
    assert counts_only({"agent_ops": null})["agent_ops"]["summary"]["agent_instruction_rewrite_reason"] == "no_agent_jobs"


def test_codeowned_reads_the_last_matching_line() -> None:
    text = "# owners\n* @org/all\n/docs/ @org/docs\n/AGENTS.md\n"
    assert codeowned(text, "src/a.py") and codeowned(text, "docs/x.md")
    assert not codeowned(text, "AGENTS.md")  # a later line with no owner clears it
    assert not codeowned("", "AGENTS.md")
    assert codeowned("CLAUDE.md @org/a  # trailing comment\n", "sub/CLAUDE.md")
    assert codeowned("**/AGENTS.md @o\n", "AGENTS.md") and codeowned("**/*.md @o\n", "CLAUDE.md")
    assert codeowned("**/AGENTS.md @o\n", "docs/AGENTS.md")
    assert not codeowned("/.github/* @o\n", ".github/workflows/a.yml")
    assert codeowned("/.github/** @o\n", ".github/workflows/a.yml")
    assert codeowned(".github/ @o\n", ".github/workflows/a.yml") and codeowned("docs/*.md @o\n", "docs/a.md")
    assert not codeowned("/docs/ @o\n", "src/docs/a.md") and codeowned("docs/ @o\n", "docs/a/b.md")


def test_a_single_star_on_the_workflow_folder_still_raises_it(git_repo) -> None:
    owners = "/AGENTS.md @o\n/CLAUDE.md @o\n/.github/* @o\n"
    repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, ".github/CODEOWNERS": owners})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1


def test_a_codeowners_that_does_not_own_itself_guards_nothing(git_repo) -> None:
    owners = f"/AGENTS.md @example-org/maintainers\n/CLAUDE.md @example-org/maintainers\n/{WORKFLOW} @o\n"
    repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, ".github/CODEOWNERS": owners})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1


def test_the_github_codeowners_file_wins_over_a_stale_root_one(git_repo) -> None:
    repo = _repo(git_repo, {WORKFLOW: SCHEDULED_WRITER, "CODEOWNERS": OWNERS,
                            ".github/CODEOWNERS": "/src/ @example-org/maintainers\n"})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1


CALLER = """on:
  schedule:
    - cron: "0 3 * * 1"
jobs:
  sync:
    permissions:
      contents: write
    uses: ./.github/workflows/agent.yml
"""

CALLED = """on: workflow_call
jobs:
  agent:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/prompt
      - run: claude -p "Update AGENTS.md" && git push
"""


def test_a_called_workflow_and_its_local_action_are_paths_at_risk(git_repo) -> None:
    caller_only = f"/AGENTS.md @o\n/CLAUDE.md @o\n/{WORKFLOW} @o\n/.github/CODEOWNERS @o\n"
    files = {WORKFLOW: CALLER, ".github/workflows/agent.yml": CALLED,
             ".github/actions/prompt/action.yml": "runs:\n  using: composite\n  steps: []\n",
             ".github/CODEOWNERS": caller_only}
    repo = _repo(git_repo, files)
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1
    owned = caller_only + "/.github/workflows/agent.yml @o\n"
    repo = _repo(git_repo, {".github/CODEOWNERS": owned})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1  # the action is still unowned
    repo = _repo(git_repo, {".github/CODEOWNERS": owned + "/.github/actions/ @o\n"})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 0


GH_AW_IMPORTS = GH_AW_PR.replace("engine: claude\n", "engine: claude\nimports:\n  - shared/style.md\n"
                                 "  - example-org/agentics/shared/remote.md@v1\n")


def test_a_gh_aw_import_and_its_own_imports_are_paths_at_risk(git_repo) -> None:
    rel = ".github/workflows/guide.md"
    source_only = f"/AGENTS.md @o\n/CLAUDE.md @o\n/{rel} @o\n/.github/CODEOWNERS @o\n"
    files = {rel: GH_AW_IMPORTS,
             ".github/workflows/shared/style.md": "---\nimports: [tone.md#Voice]\n---\nWrite plainly.\n",
             ".github/workflows/shared/tone.md": "Keep it short.\n",
             ".github/CODEOWNERS": source_only}
    repo = _repo(git_repo, files)
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1
    owned = source_only + "/.github/workflows/shared/style.md @o\n"
    repo = _repo(git_repo, {".github/CODEOWNERS": owned})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1  # the nested import is unowned
    repo = _repo(git_repo, {".github/CODEOWNERS": owned + "/.github/workflows/shared/tone.md @o\n"})
    assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 0


def test_gh_aw_imports_reads_each_list_form_and_skips_remote_imports() -> None:
    rel = ".github/workflows/guide.md"
    shared = [".github/workflows/shared/a.md", ".github/workflows/shared/b.md"]
    assert gh_aw_imports(rel, "imports: [shared/a.md, 'shared/b.md#Tone']\n") == shared
    assert gh_aw_imports(rel, "imports: [shared/a.md,\n  shared/b.md]\nengine: claude\n") == shared
    block = "imports:\n  - 'shared/a.md'  # house style\n  - shared/b.md\n  - example-org/agentics/x.md@v1\n"
    assert gh_aw_imports(rel, block) == shared
    assert gh_aw_imports(rel, "engine: claude\n") == []
    mapped = "imports:\n  - path: shared/a.md\n    inputs: {tone: plain}\n  - uses: shared/b.md\n    with:\n      x: 1\n"
    assert gh_aw_imports(rel, mapped) == shared


SOURCE = ".github/workflows/guide.md"
SOURCE_ONLY = f"/AGENTS.md @o\n/CLAUDE.md @o\n/{SOURCE} @o\n/.github/CODEOWNERS @o\n"


def _imports_case(git_repo, imports: str, shared: str) -> tuple[int | None, int | None]:
    """(hazard count with only the source owned, with the shared file owned too)."""
    source = GH_AW_PR.replace("engine: claude\n", f"engine: claude\n{imports}")
    repo = _repo(git_repo, {SOURCE: source, f".github/workflows/{shared}": "Write plainly.\n",
                            ".github/CODEOWNERS": SOURCE_ONLY})
    bare = scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"]
    repo = _repo(git_repo, {".github/CODEOWNERS": SOURCE_ONLY + f"/.github/workflows/{shared} @o\n"})
    return bare, scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"]


def test_a_mapping_import_whose_path_is_not_the_first_key_is_at_risk(git_repo) -> None:
    imports = "imports:\n  - inputs: {tone: plain}\n    path: shared/a.md\n"
    assert _imports_case(git_repo, imports, "shared/a.md") == (1, 0)


def test_a_flow_list_opening_on_the_next_line_is_at_risk(git_repo) -> None:
    assert _imports_case(git_repo, "imports:\n  [shared/a.md]\n", "shared/a.md") == (1, 0)


def test_a_local_import_holding_an_at_sign_is_at_risk(git_repo) -> None:
    assert _imports_case(git_repo, "imports: [shared/a@2.md]\n", "shared/a@2.md") == (1, 0)


def test_a_quoted_flow_path_holding_a_comma_is_one_import(git_repo) -> None:
    assert _imports_case(git_repo, "imports: ['shared/a,b.md']\n", "shared/a,b.md") == (1, 0)


def test_a_frontmatter_that_does_not_parse_raises_it_even_when_all_is_owned(git_repo) -> None:
    for bad in ("imports: [shared/a.md\n", "imports:\n  - shared/a.md\n   - shared/b.md\n",
                "imports: *shared\n"):
        source = GH_AW_PR.replace("engine: claude\n", f"engine: claude\n{bad}")
        repo = _repo(git_repo, {SOURCE: source, ".github/CODEOWNERS": OWNERS})
        assert scan_agent_ops(repo)["summary"]["agent_instruction_rewrite"] == 1, bad


def test_an_imported_file_whose_frontmatter_does_not_parse_raises_it(git_repo) -> None:
    source = GH_AW_PR.replace("engine: claude\n", "engine: claude\nimports: [shared/a.md]\n")
    files = {SOURCE: source, ".github/workflows/shared/a.md": "---\nimports: [b.md\n---\nText.\n",
             ".github/CODEOWNERS": OWNERS}
    assert scan_agent_ops(_repo(git_repo, files))["summary"]["agent_instruction_rewrite"] == 1
    files[".github/workflows/shared/a.md"] = "---\nimports: [b.md]\n---\nText.\n"
    assert scan_agent_ops(_repo(git_repo, files))["summary"]["agent_instruction_rewrite"] == 0


def test_the_hazard_names_each_file_whose_frontmatter_it_could_not_read(git_repo) -> None:
    source = GH_AW_PR.replace("engine: claude\n", "engine: claude\nimports: [shared/a.md, shared/b.md]\n")
    files = {SOURCE: source, ".github/workflows/shared/a.md": "---\nimports: [c.md\n---\nText.\n",
             ".github/workflows/shared/b.md": "---\nimports: *x\n---\nText.\n",
             ".github/CODEOWNERS": OWNERS}
    block = scan_agent_ops(_repo(git_repo, files))
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_files"] == [SOURCE]
    assert block["agent_instruction_rewrite_unread_files"] == [".github/workflows/shared/a.md",
                                                               ".github/workflows/shared/b.md"]
    kept = counts_only({"agent_ops": block})["agent_ops"]
    assert kept["agent_instruction_rewrite_unread_files"] == block["agent_instruction_rewrite_unread_files"]
    entry = next(h for h in _level(dict(block))["hazards"]  # type: ignore[arg-type]
                 if h["id"] == "agent-rewrites-instructions.1")
    assert "could not read" in entry["fix"]
    bad_source = GH_AW_PR.replace("engine: claude\n", "engine: claude\nimports: [shared/a.md\n")
    block = scan_agent_ops(_repo(git_repo, {SOURCE: bad_source}))
    assert block["agent_instruction_rewrite_unread_files"] == [SOURCE]


def test_an_owned_hazard_with_every_frontmatter_read_names_no_unread_file(git_repo) -> None:
    block = scan_agent_ops(_repo(git_repo, {WORKFLOW: SCHEDULED_WRITER}))
    assert block["summary"]["agent_instruction_rewrite"] == 1
    assert block["agent_instruction_rewrite_unread_files"] == []


def test_an_imported_file_opening_with_a_rule_and_no_closing_marker_has_no_frontmatter(git_repo) -> None:
    source = GH_AW_PR.replace("engine: claude\n", "engine: claude\nimports: [shared/a.md]\n")
    files = {SOURCE: source, ".github/workflows/shared/a.md": "---\nimports: [b.md\nText after a rule.\n",
             ".github/CODEOWNERS": SOURCE_ONLY + "/.github/workflows/shared/a.md @o\n"}
    block = scan_agent_ops(_repo(git_repo, files))
    assert block["summary"]["agent_instruction_rewrite"] == 0
    assert block["agent_instruction_rewrite_unread_files"] == []


def test_the_four_forms_github_accepts_read_as_clear_when_owned(git_repo) -> None:
    ended = GH_AW_PR.replace("create-pull-request:\n---", "create-pull-request:\n...\n---")
    for base, imports in ((GH_AW_PR, "imports: null\n"), (GH_AW_PR, "imports:\t[shared/a.md]\n"),
                          (ended, "imports: [shared/a.md]\n")):
        source = base.replace("engine: claude\n", f"engine: claude\n{imports}")
        files = {SOURCE: source, ".github/workflows/shared/a.md": "Write plainly.\n", ".github/CODEOWNERS": OWNERS}
        block = scan_agent_ops(_repo(git_repo, files))
        assert block["summary"]["agent_instruction_rewrite"] == 0, imports
        assert block["agent_instruction_rewrite_unread_files"] == [], imports
    early = GH_AW_PR.replace("engine: claude\n", "engine: claude\n...\n")  # text past the end
    files = {SOURCE: early, ".github/CODEOWNERS": OWNERS}
    assert scan_agent_ops(_repo(git_repo, files))["agent_instruction_rewrite_unread_files"] == [SOURCE]


def test_a_workflow_folder_note_with_an_unclosed_rule_does_not_break_the_scan(git_repo) -> None:
    repo = _repo(git_repo, {".github/workflows/notes.md": "---\nSome notes.\n"})
    summary = scan_agent_ops(repo)["summary"]
    assert summary["agent_instruction_rewrite"] is None
    assert summary["agent_instruction_rewrite_reason"] == "no_agent_jobs"
