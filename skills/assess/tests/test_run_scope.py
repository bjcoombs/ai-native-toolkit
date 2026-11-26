"""A run scoped to several folders or a glob (#1110): ``lib.run.run_scope.Scope``.

The type (``contains``, roots, the hash slug), the CLI errors, each scope-reading scan
reporting the union of its two single-folder readings, and the run-level union for file
counts and conditions met. A one-folder scope keeps the pre-#1110 label and slug, so its
output is unchanged (the assess goldens pin that).
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import assess_core
import assess_signals
from lib.agent_outcome import scan_agent_outcome
from lib.agents.agent_harness import scan_agent_harness
from lib.counts_only.content_free import HASH_PREFIX
from lib.docs.doc_claims import doc_claim_files
from lib.docs.doc_graph import discover_doc_files
from lib.docs.doc_staleness import discover_code_files
from lib.graph.import_graph import language_counts
from lib.history.ai_usage import scan_ai_usage
from lib.history.git_churn import git_churn_scores
from lib.instructions.claude_config import scan_claude_config
from lib.instructions.guardrails import scan_guardrails
from lib.instructions.instruction_placement import skill_and_agent_files
from lib.keyhole.liveness_scan import _count_ext
from lib.keyhole.promissory_markers import scan_promissory_markers
from lib.run import signals
from lib.run.headless_layers import source_files
from lib.run.run_scope import Scope, resolve_scope, scope_roots
from lib.testing.coverage_gate import detect_coverage_gate
from test_unit_readings import MONOREPO, _write

CLAUDE = "Claude Helper <noreply@anthropic.com>"
NO_EXCLUDES: tuple[set[str], list[str]] = (set(), [])


def _folder(name: str) -> dict[str, str]:
    """One package: docs, a marker, a coverage floor, a ruff gate, an agent and a tracked .env."""
    return {
        f"pkgs/{name}/README.md": f"# {name}\n\nSee [guide](docs/guide.md).\n",
        f"pkgs/{name}/docs/guide.md": f"# Guide {name}\n",
        f"pkgs/{name}/{name}.py": f"def f_{name}():  # TODO: tidy\n    return 1\n",
        f"pkgs/{name}/pyproject.toml": "[tool.ruff.lint]\nselect = ['C901']\n\n"
                                       "[tool.coverage.report]\nfail_under = 85\n",
        f"pkgs/{name}/.claude/agents/{name}.md": f"---\nname: {name}\ndescription: d\n---\nBody.\n",
        f"pkgs/{name}/.env": "X=1\n",
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"{message}\n\nCo-authored-by: {CLAUDE}")


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three sibling packages, each added by its own commit, under a root with a README."""
    root = _write(tmp_path_factory.mktemp("scope") / "repo", {"README.md": "# Repo\n", "CLAUDE.md": "# R\n"})
    for name in ("a", "b", "c"):
        _commit(root, _folder(name), f"add {name}")
    return root


def _scope(repo: Path, *raw: str) -> Scope:
    scope = resolve_scope(repo, [Path(r) for r in raw])[0]
    assert scope is not None
    return scope


# --- the type -------------------------------------------------------------------------

def test_two_folders_hold_both_roots_and_contain_only_their_files(repo: Path) -> None:
    scope = _scope(repo, "pkgs/b", "pkgs/a")
    assert scope.patterns == ("pkgs/a", "pkgs/b")
    assert scope.roots == ((repo / "pkgs/a").resolve(), (repo / "pkgs/b").resolve())
    assert scope_roots(scope, repo) == scope.roots
    assert scope_roots(None, repo) == (repo.resolve(),)
    assert scope.contains(repo / "pkgs/a/a.py") and scope.contains(Path("pkgs/b/b.py"))
    assert not scope.contains(repo / "pkgs/c/c.py") and not scope.contains(repo / "README.md")
    assert scope.label == "pkgs/a,pkgs/b"


def test_a_glob_names_every_folder_it_matches(repo: Path) -> None:
    scope = _scope(repo, "pkgs/*")
    assert scope.patterns == ("pkgs/*",)
    assert [r.name for r in scope.roots] == ["a", "b", "c"]
    assert scope.contains(repo / "pkgs/c/c.py") and not scope.contains(repo / "README.md")


def test_a_root_inside_another_is_dropped_from_the_walk_but_kept_as_a_folder(repo: Path) -> None:
    scope = _scope(repo, "pkgs", "pkgs/a")
    assert scope.roots == ((repo / "pkgs").resolve(),)
    assert scope.folders == ((repo / "pkgs").resolve(), (repo / "pkgs/a").resolve())


def test_a_glob_keeps_folders_only_and_skips_excluded_trees(repo: Path) -> None:
    scope = _scope(repo, "*")
    assert [r.relative_to(repo.resolve()).as_posix() for r in scope.roots] == ["pkgs"]
    with pytest.raises(ValueError, match=r"matches nothing: \*\.md"):
        _scope(repo, "*.md")


def test_an_existing_folder_with_glob_characters_is_a_path(tmp_path: Path) -> None:
    (tmp_path / "app" / "[locale]").mkdir(parents=True)
    scope, label, slug = resolve_scope(tmp_path, [Path("app/[locale]")])
    assert scope is not None and scope.roots == ((tmp_path / "app" / "[locale]").resolve(),)
    assert (label, slug) == ("app/[locale]", "app-[locale]")


def test_nested_folders_keep_their_own_docs(tmp_path: Path) -> None:
    root = _write(tmp_path / "n", {"pkgs/a/docs/x.md": "# x\n", "pkgs/a/tools/docs/y.md": "# y\n"})
    both = set(doc_claim_files(root, NO_EXCLUDES, _scope(root, "pkgs/a", "pkgs/a/tools")))
    assert both == {"pkgs/a/docs/x.md", "pkgs/a/tools/docs/y.md"}


def test_the_slug_is_stable_under_reordering_and_keeps_one_folder_unchanged(repo: Path) -> None:
    two = _scope(repo, "pkgs/a", "pkgs/b").slug
    assert two == _scope(repo, "pkgs/b", "pkgs/a").slug
    assert two.startswith("scope-") and len(two) == len("scope-") + 12
    assert _scope(repo, "pkgs/*").slug.startswith("scope-") and _scope(repo, "pkgs/*").slug != two
    assert resolve_scope(repo, Path("pkgs/a"))[1:] == ("pkgs/a", "pkgs-a")  # pre-#1110 label and slug
    assert Scope.of(repo, repo / "pkgs/a") == _scope(repo, "pkgs/a")


def test_a_pattern_that_matches_nothing_is_an_error_naming_it(repo: Path) -> None:
    with pytest.raises(ValueError, match=r"matches nothing: nope/\*"):
        resolve_scope(repo, [Path("pkgs/a"), Path("nope/*")])


def test_the_signals_cli_exits_2_naming_an_unmatched_pattern(
        repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "x.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--scope", "pkgs/a", "--scope", "nope/*"]) == 2
    assert "nope/*" in capsys.readouterr().err
    assert not out.exists()


def test_the_core_cli_exits_2_naming_an_unmatched_pattern(
        repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert assess_core.main([str(repo), "--non-interactive", "--scope", "nope/*"]) == 2
    assert "nope/*" in capsys.readouterr().err
    assert not (repo / ".assess").exists()


# --- each scan reads the union of its two single-folder readings ----------------------

def _rels(repo: Path, paths: Any) -> set[str]:
    return {Path(p).resolve().relative_to(repo.resolve()).as_posix() for p in paths}


Reading = Callable[[Path, Scope], Any]

UNION_READINGS: dict[str, Reading] = {
    "doc_graph": lambda r, s: _rels(r, discover_doc_files(r, scope=s)),
    "doc_staleness": lambda r, s: _rels(r, discover_code_files(r, scope=s)),
    "doc_claims": lambda r, s: set(doc_claim_files(r, NO_EXCLUDES, s)),
    "instruction_placement": lambda r, s: _rels(r, (p for p, _ in skill_and_agent_files(r, s))),
    "coverage_gate": lambda r, s: {g["file"] for g in detect_coverage_gate(r, s)["gates"]},
    "guardrails": lambda r, s: {g["file"] for g in scan_guardrails(r, [], None, None, s)["gates"]},
    "agent_harness": lambda r, s: {(f["kind"], f["file"]) for f in scan_agent_harness(r, s)["findings"]},
    "git_churn": lambda r, s: _rels(r, git_churn_scores(r, scope=s)),
    "headless_size": lambda r, s: _rels(r, source_files(r, NO_EXCLUDES, s)),
}

SUM_READINGS: dict[str, Reading] = {
    "claude_config": lambda r, s: scan_claude_config(r, s)["files_scanned"],
    "liveness": lambda r, s: _count_ext(r, {".py"}, scope=s),
    "promissory_markers": lambda r, s: scan_promissory_markers(r, scope=s).summary()["total_markers"],
    "import_graph": lambda r, s: sum(language_counts(r, scope=s).values()),
    "ai_usage": lambda r, s: scan_ai_usage(r, s, None)["total_commits"],
    "agent_outcome": lambda r, s: scan_agent_outcome(r, s, None)["agent_commits"],
}


@pytest.mark.parametrize("name", sorted(UNION_READINGS))
def test_a_two_folder_scan_reads_the_union_of_its_folders(repo: Path, name: str) -> None:
    read = UNION_READINGS[name]
    a, b = read(repo, _scope(repo, "pkgs/a")), read(repo, _scope(repo, "pkgs/b"))
    assert a and b and a != b  # each folder has its own reading
    assert read(repo, _scope(repo, "pkgs/a", "pkgs/b")) == a | b


@pytest.mark.parametrize("name", sorted(SUM_READINGS))
def test_a_two_folder_count_is_the_sum_of_its_disjoint_folders(repo: Path, name: str) -> None:
    read = SUM_READINGS[name]
    a, b = read(repo, _scope(repo, "pkgs/a")), read(repo, _scope(repo, "pkgs/b"))
    assert a > 0 and b > 0
    assert read(repo, _scope(repo, "pkgs/a", "pkgs/b")) == a + b


# --- the run: file counts and conditions met -------------------------------------------

@pytest.fixture(scope="module")
def mono(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write(tmp_path_factory.mktemp("mono") / "repo", MONOREPO)


def _met(repo: Path, *raw: str) -> set[str]:
    with contextlib.redirect_stderr(io.StringIO()):
        ctx = assess_core.build_run_context(repo_root=repo, run_date="2026-11-26", non_interactive=True,
                                            scope=[Path(r) for r in raw])
    return {c["id"] for c in ctx["level"]["conditions"] if c["state"] == "met"}


def test_a_two_folder_run_meets_the_union_of_its_folders_conditions(mono: Path) -> None:
    billing, web = _met(mono, "services/billing"), _met(mono, "web")
    assert billing != web
    assert _met(mono, "services/billing", "web") == billing | web


def _files_scored(repo: Path, *raw: str) -> int:
    doc = signals.build_document(repo, ["stats_summary"], scope=resolve_scope(repo, [Path(r) for r in raw])[0])
    scored: int = doc["scans"]["stats_summary"]["files_scored"]
    return scored


def test_a_two_folder_run_scores_the_files_of_both(mono: Path) -> None:
    pytest.importorskip("lizard")
    billing, web = _files_scored(mono, "services/billing"), _files_scored(mono, "web")
    assert billing and web
    assert _files_scored(mono, "services/billing", "web") == billing + web


def test_a_scoped_document_is_the_same_under_every_jobs_width(mono: Path) -> None:
    scope = resolve_scope(mono, [Path("services/*")])[0]
    one = signals.build_document(mono, scope=scope, jobs=1)
    wide = signals.build_document(mono, scope=scope)
    assert json.dumps(one, sort_keys=True, default=str) == json.dumps(wide, sort_keys=True, default=str)
    assert one["stamp"]["scope"] == "services/*"


# --- counts-only ------------------------------------------------------------------------

def _stamp(repo: Path, out: Path, *extra: str) -> Any:
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", "stats_summary",
                                "--scope", "services/billing", "--scope", "web", *extra]) == 0
    return json.loads(out.read_text(encoding="utf-8"))["stamp"]["scope"]


def test_counts_only_writes_set_or_the_hash_for_a_two_folder_scope(mono: Path, tmp_path: Path) -> None:
    assert _stamp(mono, tmp_path / "p.json") == "services/billing,web"
    assert _stamp(mono, tmp_path / "c.json", "--counts-only") == assess_signals.SCOPE_REDACTED == "set"
    assert _stamp(mono, tmp_path / "h.json", "--counts-only", "--hash-paths").startswith(HASH_PREFIX)
