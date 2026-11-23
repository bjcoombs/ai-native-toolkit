"""The tour's sample ships as inert data (#1149).

``tour/sample.toml`` holds the whole sample repository (files, history, fix commit) and
``tour/fix.patch`` the one fix. Three promises:

- the move from the #1144 fixture tree changed nothing a newcomer sees: the same commits
  (pinned SHAs) before and after the fix;
- a scan of a repository that carries the two files at their shipped path reads the same as
  one without them, apart from the archetype's count of "other" files, and no reading names
  the path: not the instruction files, the CI reader, the doc graph, the complexity files or
  the archetype's code and doc counts;
- the standalone ZIP carries both files, and the tour runs end to end from the unpacked ZIP
  outside this repository: build, run, fix, re-run, and the Step moves from 2 to 3.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import assess_tour
from assess_core import build_and_publish_run
from golden import normalize_run_context

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = "skills/assess/tour"
# The sample's HEAD before and after the fix, as the #1144 fixture tree built them.
SAMPLE_HEAD = "f2dee5aa81be46cd8787b0d89be85acfc4426896"
FIXED_HEAD = "1232fffbe0a885d7338cb68c3cfae9b49042cfff"


def _head(repo: Path) -> str:
    env = assess_tour._git_env("A <a@example.com>", "2026-10-01T00:00:00+00:00")
    return assess_tour._git(repo, env, "rev-parse", "HEAD").strip()


def test_the_move_keeps_the_sample_commits(tmp_path: Path) -> None:
    assert _head(assess_tour.build(tmp_path / "a")) == SAMPLE_HEAD
    assert _head(assess_tour.build(tmp_path / "b", fixed=True)) == FIXED_HEAD


def test_every_sample_file_is_committed_once() -> None:
    history = assess_tour.load_history()
    committed = [path for commit in history["commits"] for path in commit["paths"]]
    assert sorted(committed) == sorted(assess_tour.sample_files(history))
    assert len(committed) == len(set(committed))


def _project(root: Path, with_tour: bool) -> Path:
    """The sample's own files as one commit, with the shipped tour files beside them or not."""
    files = assess_tour.sample_files(assess_tour.load_history())
    # The shipped files sit in a skill directory, so both repositories have one, and the
    # skills scan reads the same in each.
    files["skills/assess/SKILL.md"] = "---\nname: assess\ndescription: A stand-in skill.\n---\n"
    for rel, text in files.items():
        assess_tour._write_lf(text, root / rel)
    if with_tour:
        for name in ("sample.toml", "fix.patch"):
            target = root / SHIPPED / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(assess_tour.SAMPLE_DIR / name, target)
    env = assess_tour._git_env("A <a@example.com>", "2026-10-01T00:00:00+00:00")
    assess_tour._git(root, env, "init", "-q", "-b", "main")
    assess_tour._git(root, env, "add", "-A")
    assess_tour._git(root, env, "commit", "-q", "-m", "Project")
    return root


def _differences(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            out += _differences(left.get(key), right.get(key), f"{path}.{key}")
        return out
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [d for i, (a, b) in enumerate(zip(left, right)) for d in _differences(a, b, f"{path}[{i}]")]
    return [] if left == right else [path]


@pytest.fixture(scope="module")
def readings(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, with_tour in (("without", False), ("with", True)):
        root = _project(tmp_path_factory.mktemp(label) / "project", with_tour)
        build_and_publish_run(repo_root=root, run_date="2026-10-01", non_interactive=True)
        ctx = json.loads((root / ".assess" / "run-context.json").read_text(encoding="utf-8"))
        out[label] = normalize_run_context(ctx)
    return out


def test_no_reading_names_the_shipped_sample(readings: dict[str, dict[str, Any]]) -> None:
    assert SHIPPED not in json.dumps(readings["with"])
    assert "sample.toml" not in json.dumps(readings["with"])


def test_the_shipped_sample_moves_only_the_other_file_count(readings: dict[str, dict[str, Any]]) -> None:
    # The two files are data: the archetype counts them as "other" files, never as code or
    # docs, and nothing else reads them. Commit SHAs differ with the tree, so they are masked.
    diffs = _differences(readings["without"], readings["with"])
    diffs = [d for d in diffs if not d.endswith((".head_sha", ".head_short", ".sha", ".commit"))]
    assert diffs == [".archetype.signals.other_file_count"]
    without, with_tour = readings["without"]["archetype"], readings["with"]["archetype"]
    assert with_tour["signals"]["other_file_count"] == without["signals"]["other_file_count"] + 2


def test_the_complexity_scan_scores_no_sample_file(tmp_path: Path) -> None:
    # The treemap's per-function backend is lizard over the whole root (lizard_scores): it
    # reads the files whose language it knows, and TOML and patch files are not among them.
    import lizard

    root = _project(tmp_path / "project", with_tour=True).resolve()
    scored = [Path(f.filename).resolve().relative_to(root).as_posix()
              for f in lizard.analyze(paths=[str(root)], exclude_pattern=[])]
    assert "src/tally/ledger.py" in scored
    assert not [p for p in scored if p.startswith(SHIPPED)]


def _standalone_zip(dest: Path) -> Path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from standalone_skill_config import SKILLS
        from transform_skill import build_standalone_skill_zip
    finally:
        sys.path.pop(0)
    cfg = SKILLS["assess"]
    out = dest / "assess.zip"
    issues = build_standalone_skill_zip(
        skill_source_dir=REPO_ROOT / cfg["source_dir"], out_zip=out,
        standalone_name=cfg["standalone_name"], standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"], exclude_dirs=frozenset(cfg["exclude_dirs"]),
        bundle_files={k: REPO_ROOT / v for k, v in cfg.get("bundle_files", {}).items()},
    )
    assert issues == []
    return out


def _tour(script: Path, cwd: Path, *args: str) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, check=True, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    result: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return result


def test_the_tour_runs_from_the_unpacked_standalone_zip(tmp_path: Path) -> None:
    archive = _standalone_zip(tmp_path)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert {"assess/tour/sample.toml", "assess/tour/fix.patch"} <= names
        guide = zf.read("assess/references/tour.md").decode("utf-8")
        skill = zf.read("assess/SKILL.md").decode("utf-8")
    # The ZIP's guide and skill name the tour with commands that run there.
    assert 'uv run scripts/assess_tour.py build "$(mktemp -d)/tally"' in guide
    assert "CLAUDE_SKILL_DIR" not in guide and "chat-" not in guide
    assert "`--tour` (the guided first run)" in skill and "$ARGUMENTS" not in skill
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp_path / "unpacked")
    script = tmp_path / "unpacked" / "assess" / "scripts" / "assess_tour.py"
    work = tmp_path / "elsewhere"
    work.mkdir()
    repo = Path(_tour(script, work, "build", str(work / "tally"))["repo"])
    assert _tour(script, work, "run", str(repo))["step"] == 2
    assert _tour(script, work, "fix", str(repo))["fix"] == "applied"
    after = _tour(script, work, "run", str(repo))
    assert (after["step"], after["next_gates"]) == (3, ["coverage_gate"])
