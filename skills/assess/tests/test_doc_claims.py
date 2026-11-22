"""Tests for lib/doc_claims.py - claims across the doc set, and between documents.

The instruction-claim kinds (enforcement, pin, count) run over ``docs/``,
``skills/*/references/`` and ``skills/*/SKILL.md``; the cross-document kind
flags two files giving the same checkable claim different values, citing both
lines.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.doc_claims import doc_claim_files, find_contradictions, scan_doc_claims
from lib.instruction_claims import extract_claims, scan_instruction_claims

PIN_311 = "# Setup\n\nPython 3.11 is pinned in `.python-version`.\n"
PIN_312 = "# Setup\n\nRead this first.\n\nPython 3.12 is pinned in `.python-version`.\n"


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _sites(contradiction: dict) -> list[list]:
    return [[s["file"], s["line"], s["value"]] for s in contradiction["sites"]]


def test_scope_is_docs_skill_references_and_skill_files_minus_history(tmp_path: Path) -> None:
    for rel in ("docs/guide.md", "docs/sub/deep.md", "skills/a/SKILL.md",
                "skills/a/references/ref.md", "skills/a/references/x/y.md",
                "docs/design/old.md", "docs/superpowers/plan.md", "docs/index.html",
                "skills/a/notes.md", "README.md", "docs/node_modules/z.md"):
        _write(tmp_path, rel, "text\n")
    assert doc_claim_files(tmp_path) == [
        "docs/guide.md", "docs/sub/deep.md", "skills/a/SKILL.md",
        "skills/a/references/ref.md", "skills/a/references/x/y.md",
    ]


def test_historical_trees_match_from_the_root_only(tmp_path: Path) -> None:
    # A nested `design/` directory is not a historical record.
    _write(tmp_path, "docs/guide/design/now.md", "text\n")
    assert doc_claim_files(tmp_path) == ["docs/guide/design/now.md"]


@pytest.mark.parametrize("tree", ["docs/adr", "docs/decisions", "docs/rfcs"])
def test_decision_records_are_historical(tmp_path: Path, tree: str) -> None:
    _write(tmp_path, f"{tree}/0001.md", "text\n")
    assert doc_claim_files(tmp_path) == []


def test_scope_globs_from_the_scope_directory(tmp_path: Path) -> None:
    _write(tmp_path, "docs/root.md", "text\n")
    _write(tmp_path, "svc/docs/guide.md", "text\n")
    _write(tmp_path, "svc/docs/design/old.md", "text\n")
    assert doc_claim_files(tmp_path, None, tmp_path / "svc") == ["svc/docs/guide.md"]
    _write(tmp_path, "svc/docs/a.md", PIN_311)
    _write(tmp_path, "svc/docs/b.md", PIN_312)
    block = scan_doc_claims(tmp_path, [], tmp_path / "svc")
    assert [s["file"] for s in block["contradictions"][0]["sites"]] == [
        "svc/docs/a.md", "svc/docs/b.md"]


def test_user_excludes_apply(tmp_path: Path) -> None:
    _write(tmp_path, "docs/keep.md", "text\n")
    _write(tmp_path, "docs/drafts/skip.md", "text\n")
    _write(tmp_path, "docs/skip-me.md", "text\n")
    assert doc_claim_files(tmp_path, ({"drafts"}, ["skip-*.md"])) == ["docs/keep.md"]


def test_untracked_doc_is_ignored_in_a_git_repo(tmp_path: Path) -> None:
    import subprocess

    _write(tmp_path, "docs/tracked.md", "text\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/tracked.md"], check=True)
    _write(tmp_path, "docs/untracked.md", "text\n")
    assert doc_claim_files(tmp_path.resolve()) == ["docs/tracked.md"]


def test_a_false_pin_in_a_doc_is_a_failure(tmp_path: Path) -> None:
    _write(tmp_path, ".python-version", "3.12.1\n")
    _write(tmp_path, "docs/setup.md", PIN_311)
    block = scan_doc_claims(tmp_path, [])
    assert (block["files_scanned"], block["total"], block["failed"]) == (1, 1, 1)
    failure = block["failures"][0]
    assert (failure["file"], failure["line"], failure["kind"]) == ("docs/setup.md", 3, "pin")
    assert {"docs/design", "docs/superpowers", "docs/adr"} <= set(block["excluded_trees"])


def test_two_docs_pinning_different_versions_cite_both_lines(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", PIN_311)
    _write(tmp_path, "docs/sub/b.md", PIN_312)
    block = scan_doc_claims(tmp_path, [])
    # The pinned file is missing, so both fail against the repo; they also
    # disagree with each other.
    assert block["contradictions_total"] == 1
    found = block["contradictions"][0]
    assert (found["kind"], found["path"]) == ("pin", ".python-version")
    assert _sites(found) == [["docs/a.md", 3, "3.11"], ["docs/sub/b.md", 5, "3.12"]]
    json.dumps(block)


def test_an_instruction_file_takes_part_but_is_not_reverified(tmp_path: Path) -> None:
    _write(tmp_path, ".python-version", "3.11.9\n")
    _write(tmp_path, "CLAUDE.md", PIN_311)
    _write(tmp_path, "docs/setup.md", PIN_312)
    block = scan_doc_claims(tmp_path, ["CLAUDE.md"])
    assert (block["files_scanned"], block["total"], block["failed"]) == (1, 1, 1)
    assert _sites(block["contradictions"][0]) == [
        ["CLAUDE.md", 3, "3.11"], ["docs/setup.md", 5, "3.12"]]


def test_a_doc_that_is_also_an_instruction_file_is_left_to_instruction_claims(tmp_path: Path) -> None:
    _write(tmp_path, "docs/CLAUDE.md", PIN_311)
    block = scan_doc_claims(tmp_path, ["docs/CLAUDE.md"])
    assert (block["files_scanned"], block["total"]) == (0, 0)
    assert scan_instruction_claims(tmp_path, ["docs/CLAUDE.md"])["failed"] == 1


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_a_symlinked_doc_is_one_file_not_a_contradiction(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", PIN_311 + "\nPython 3.12 is pinned in `.python-version`.\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "AGENTS.md").symlink_to(tmp_path / "CLAUDE.md")
    block = scan_doc_claims(tmp_path, ["CLAUDE.md"])
    # The file disagrees only with itself: not a cross-document finding.
    assert block["contradictions_total"] == 0
    assert block["files_scanned"] == 0


@pytest.mark.parametrize(("a", "b", "clash"), [
    ("3.11", "3.11.9", False),
    ("3.11.9", "3.11", False),
    ("1.2", "11.2", True),
    ("3.11", "3.12", True),
    ("20.11.0", "20.11.0", False),
])
def test_versions_agree_by_dotted_prefix(tmp_path: Path, a: str, b: str, clash: bool) -> None:
    _write(tmp_path, "docs/a.md", f"Python {a} is pinned in `.python-version`.\n")
    _write(tmp_path, "docs/b.md", f"Python {b} is pinned in `.python-version`.\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == int(clash)


@pytest.mark.parametrize(("a", "b", "clash"), [(43, 45, False), (43, 60, True), (3, 5, False)])
def test_counts_agree_within_the_per_file_tolerance(tmp_path: Path, a: int, b: int, clash: bool) -> None:
    _write(tmp_path, "docs/a.md", f"There are {a} suites in `tests/*.sql`.\n")
    _write(tmp_path, "docs/b.md", f"There are {b} suites in `tests/*.sql`.\n")
    block = scan_doc_claims(tmp_path, [])
    assert block["contradictions_total"] == int(clash)
    # The counted directory does not exist: unverifiable against the repo, yet
    # the two documents can still be compared.
    assert block["total"] == 0


def test_three_files_make_one_finding_listing_every_site(tmp_path: Path) -> None:
    for name, n in (("a", 10), ("b", 40), ("c", 10)):
        _write(tmp_path, f"docs/{name}.md", f"Keep {n} suites in `tests/*.sql`.\n")
    block = scan_doc_claims(tmp_path, [])
    assert block["contradictions_total"] == 1
    assert _sites(block["contradictions"][0]) == [
        ["docs/a.md", 1, 10], ["docs/b.md", 1, 40], ["docs/c.md", 1, 10]]


def test_true_pins_of_different_tools_in_one_file_do_not_contradict(tmp_path: Path) -> None:
    _write(tmp_path, ".tool-versions", "nodejs 20.11.0\npython 3.12.1\n")
    _write(tmp_path, "docs/a.md", "Node 20.11.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/b.md", "Python 3.12.1 is pinned in `.tool-versions`.\n")
    block = scan_doc_claims(tmp_path, [])
    assert (block["verified"], block["contradictions_total"]) == (2, 0)


def test_a_pin_the_file_does_not_hold_still_contradicts(tmp_path: Path) -> None:
    _write(tmp_path, ".python-version", "3.12.1\n")
    _write(tmp_path, "docs/a.md", PIN_311)
    _write(tmp_path, "docs/b.md", PIN_312)
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 1


@pytest.mark.parametrize("tool_versions", ["nodejs 20.11.0\npython 3.12.1\n", None])
def test_pins_of_different_tools_in_one_file_never_contradict(
        tmp_path: Path, tool_versions: str | None) -> None:
    # Issue #610: the Python pin is stale (or the file is missing), yet a Node
    # pin is no evidence against it.
    if tool_versions is not None:
        _write(tmp_path, ".tool-versions", tool_versions)
    _write(tmp_path, "CLAUDE.md", "Node 20.11.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/setup.md", "Python 3.11.4 is pinned in `.tool-versions`.\n")
    block = scan_doc_claims(tmp_path, ["CLAUDE.md"])
    assert block["failed"] == 1
    assert block["contradictions_total"] == 0


def test_a_same_tool_disagreement_in_a_shared_file_still_contradicts(tmp_path: Path) -> None:
    _write(tmp_path, ".tool-versions", "nodejs 20.11.0\npython 3.12.1\n")
    _write(tmp_path, "CLAUDE.md", "Node 20.11.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/a.md", "Python 3.12.1 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/b.md", "Node.js 18.19.0 is pinned in `.tool-versions`.\n")
    block = scan_doc_claims(tmp_path, ["CLAUDE.md"])
    assert block["contradictions_total"] == 1
    assert _sites(block["contradictions"][0]) == [
        ["CLAUDE.md", 1, "20.11.0"], ["docs/b.md", 1, "18.19.0"]]


@pytest.mark.parametrize(("pin_file", "a", "b"), [
    (".python-version", "Python 3.11", "CPython 3.12"),
    (".nvmrc", "Node 20.11.0", "Node LTS 18.19.0"),
    (".python-version", "Python 3.11", "The runtime 3.12"),
    (".ruby-version", "Ruby 3.2.2", "MRI 3.3.0"),
])
def test_a_single_tool_pin_file_is_never_split_by_wording(
        tmp_path: Path, pin_file: str, a: str, b: str) -> None:
    _write(tmp_path, "docs/a.md", f"{a} is pinned in `{pin_file}`.\n")
    _write(tmp_path, "docs/b.md", f"{b} is pinned in `{pin_file}`.\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 1


def test_sdkmanrc_holds_one_sdk_per_line_so_its_pins_split_by_tool(tmp_path: Path) -> None:
    _write(tmp_path, ".sdkmanrc", "java=17.0.2-tem\nmaven=3.9.6\n")
    _write(tmp_path, "docs/a.md", "Java 17.0.2 is pinned in `.sdkmanrc`.\n")
    _write(tmp_path, "docs/b.md", "Maven 3.8.6 is pinned in `.sdkmanrc`.\n")
    block = scan_doc_claims(tmp_path, [])
    assert (block["failed"], block["contradictions_total"]) == (1, 0)


@pytest.mark.parametrize("stale", [
    "Node is pinned in `.tool-versions` as 18.19.0.",
    "Node is pinned in `.tool-versions` (currently 18.19.0).",
])
def test_a_function_word_before_the_version_is_not_a_second_tool(
        tmp_path: Path, stale: str) -> None:
    _write(tmp_path, "docs/a.md", "Node 20.11.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/b.md", stale + "\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 1


def test_a_pin_naming_no_tool_joins_the_one_tool_named(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "Node 20.11.0 is pinned in `.nvmrc`.\n")
    _write(tmp_path, "docs/b.md", "Version 18.19.0 is pinned in `.nvmrc`.\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 1


def test_a_pin_naming_no_tool_is_not_compared_when_several_are_named(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "Node 20.11.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/b.md", "Python 3.12.1 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/c.md", "Version 18.19.0 is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/d.md", "Version 3.11.4 is pinned in `.tool-versions`.\n")
    block = scan_doc_claims(tmp_path, [])
    # Unnamed pins are not compared with each other either: 18.19.0 and 3.11.4
    # may be two tools. Each is still verified on its own.
    assert block["contradictions_total"] == 0
    assert block["failed"] == 4


@pytest.mark.parametrize(("node", "python"), [
    ("`node` 20.11.0", "`python` 3.11.4"),
    ("**Node** 20.11.0", "**Python** 3.11.4"),
    ("Node >= 20.11.0", "Python ~= 3.11.4"),
])
def test_markdown_and_operator_forms_still_split_by_tool(
        tmp_path: Path, node: str, python: str) -> None:
    _write(tmp_path, ".tool-versions", "nodejs 20.11.0\npython 3.12.1\n")
    _write(tmp_path, "CLAUDE.md", f"{node} is pinned in `.tool-versions`.\n")
    _write(tmp_path, "docs/setup.md", f"{python} is pinned in `.tool-versions`.\n")
    assert scan_doc_claims(tmp_path, ["CLAUDE.md"])["contradictions_total"] == 0


@pytest.mark.parametrize(("pin_file", "content", "a", "b"), [
    (".python-version", "11.2.0\n", "Python 1.2", "Python 11.2.0"),
    (".tool-versions", "nodejs 20.11.0\n", "Node 20.1", "Node 20.11.0"),
])
def test_a_version_inside_a_longer_one_does_not_reconcile(
        tmp_path: Path, pin_file: str, content: str, a: str, b: str) -> None:
    _write(tmp_path, pin_file, content)
    _write(tmp_path, "docs/a.md", f"{a} is pinned in `{pin_file}`.\n")
    _write(tmp_path, "docs/b.md", f"{b} is pinned in `{pin_file}`.\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 1


def test_skill_docs_resolve_paths_against_their_skill(tmp_path: Path) -> None:
    for i in range(8):
        _write(tmp_path, f"skills/b/references/g{i}.md", "text\n")
    for i in range(3):
        _write(tmp_path, f"skills/a/references/g{i}.md", "text\n")
        _write(tmp_path, f"references/r{i}.md", "text\n")
    _write(tmp_path, "skills/a/SKILL.md", "Read the 3 guides in `references/*.md`.\n")
    _write(tmp_path, "skills/b/SKILL.md", "Read the 8 guides in `references/*.md`.\n")
    block = scan_doc_claims(tmp_path, [])
    # Two skills, two directories: both true, and no contradiction between them.
    assert (block["total"], block["verified"], block["contradictions_total"]) == (2, 2, 0)


def test_a_skill_doc_falls_back_to_the_root_when_only_that_exists(tmp_path: Path) -> None:
    _write(tmp_path, ".nvmrc", "20.11.0\n")
    _write(tmp_path, "skills/a/references/setup.md", "Node 20.11.0 is pinned in `.nvmrc`.\n")
    block = scan_doc_claims(tmp_path, [])
    assert (block["total"], block["verified"]) == (1, 1)


def test_a_doc_outside_skills_is_not_rebased(tmp_path: Path) -> None:
    from lib.doc_claims import rebase_skill_claims

    claims = extract_claims("Keep 3 suites in `tests/*.sql`.\n")
    assert rebase_skill_claims(tmp_path, "docs/a.md", claims) == claims
    assert rebase_skill_claims(tmp_path, "skills/a/notes/x.md", claims) == claims


def test_conflict_pair_skips_agreeing_sites_in_the_same_file(tmp_path: Path) -> None:
    # A.md states 3.11 twice; the first two sites agree, so the cited pair is
    # A.md's first line against B.md, never two lines of A.md.
    _write(tmp_path, "docs/A.md", PIN_311 + "\nPython 3.11 is pinned in `.python-version`.\n")
    _write(tmp_path, "docs/B.md", PIN_312)
    found = scan_doc_claims(tmp_path, [])["contradictions"][0]
    assert len(found["sites"]) == 3
    assert [[s["file"], s["line"], s["value"]] for s in found["conflict_pair"]] == [
        ["docs/A.md", 3, "3.11"], ["docs/B.md", 5, "3.12"]]


def test_different_paths_and_enforcement_claims_never_contradict(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "Python 3.11 is pinned in `.python-version`.\n"
                                  "`scripts/x.sh` is enforced in CI.\n")
    _write(tmp_path, "docs/b.md", "Python 3.12 is pinned in `.tool-versions`.\n"
                                  "`scripts/x.sh` is enforced in CI.\n")
    assert scan_doc_claims(tmp_path, [])["contradictions_total"] == 0


def test_contradiction_list_is_capped_but_the_total_is_not(tmp_path: Path) -> None:
    from lib import doc_claims

    a = "\n\n".join(f"Keep 10 suites in `t{i}/*.sql`." for i in range(30))
    b = "\n\n".join(f"Keep 90 suites in `t{i}/*.sql`." for i in range(30))
    _write(tmp_path, "docs/a.md", a + "\n")
    _write(tmp_path, "docs/b.md", b + "\n")
    block = scan_doc_claims(tmp_path, [])
    assert block["contradictions_total"] == 30
    assert len(block["contradictions"]) == doc_claims.MAX_CONTRADICTIONS


def test_unreadable_and_missing_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "bad.md").write_bytes(b"\xff\xfe\x00bad")
    block = scan_doc_claims(tmp_path, ["CLAUDE.md"])
    assert (block["files_scanned"], block["total"], block["contradictions_total"]) == (0, 0, 0)


def test_find_contradictions_ignores_kinds_without_a_value() -> None:
    claims = extract_claims("`scripts/x.sh` is enforced in CI.\n")
    assert find_contradictions([("a.md", Path("/a.md"), claims),
                                ("b.md", Path("/b.md"), claims)]) == []


def test_build_run_context_carries_the_block(tmp_path: Path) -> None:
    from assess_core import build_and_publish_run

    _write(tmp_path, "docs/a.md", PIN_311)
    _write(tmp_path, "docs/b.md", PIN_312)
    ctx = build_and_publish_run(repo_root=tmp_path, run_date="2026-11-20")
    assert ctx["doc_claims"]["contradictions_total"] == 1
    written = json.loads((tmp_path / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    assert written["doc_claims"]["files_scanned"] == 2
