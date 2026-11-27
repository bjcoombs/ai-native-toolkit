"""Tests for lib/history/concurrency.py: touch share, files per merge and generated churn (#985).

Real git fixtures with fixed dates: a registry every merge edits reads above the threshold
with its fan-in, merges on disjoint files read nothing above it, a linguist-generated file
counts as generated churn; this repository at a fixed commit reads the generated catalog
pair above the threshold and the median git itself gives; short and shallow histories read
not measured; counts-only output keeps every count and no path; a pooled run writes the
same block as --jobs 1; and no maturity-model row reads the block.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.counts_only.content_free import counts_only_scans  # noqa: E402
from lib.history.concurrency import (  # noqa: E402
    LIMITS,
    MIN_MERGES,
    NONE_ABOVE_THRESHOLD,
    TOUCH_SHARE_THRESHOLD,
    concurrency_block,
    scan_concurrency,
)
from lib.run.run_scope import resolve_scope  # noqa: E402
from lib.run.scan_registry import SCANS  # noqa: E402
from lib.run_context_types_concurrency import ConcurrencyBlock  # noqa: E402

DAY = 86400
T0 = 1_700_000_000  # a fixed epoch: the fixtures never depend on today
TOOLKIT = Path(__file__).resolve().parents[3]
# A first-parent commit of main from the 2026-11-24 self-assessment (#985 comment), when
# every check change committed the regenerated catalog pair.
FIXED_COMMIT = "600cca4ec653552f2e82d19d4dfd54fcab703ca4"
GENERATED_PAIR = ("docs/what-we-measure.md", "skills/assess/references/measures.json")


def _git(repo: Path, *args: str, at: int = T0) -> str:
    stamp = f"@{at} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env).stdout


def _commit(repo: Path, files: dict[str, str], at: int) -> None:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A", at=at)
    _git(repo, "commit", "-q", "-m", f"change {sorted(files)[0]}", at=at)


def _init(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.name", "Ada Person")
    _git(r, "config", "user.email", "ada.person@example.org")
    return r


def _ok(block: object) -> ConcurrencyBlock:
    assert isinstance(block, dict) and block["available"] is True, block
    return block  # type: ignore[return-value]


@pytest.fixture
def registry_repo(tmp_path: Path) -> Path:
    """Twelve merges: each adds a feature file in its own folder and edits the registry;
    every third also regenerates a linguist-generated catalog."""
    r = _init(tmp_path)
    (r / ".gitattributes").write_text("catalog.json linguist-generated\n", encoding="utf-8")
    for i in range(12):
        files = {f"feature{i}/main.py": f"x = {i}\n", "registry.py": f"N = {i}\n"}
        if i % 3 == 0:
            files["catalog.json"] = f'{{"n": {i}}}\n'
        _commit(r, files, T0 + i * DAY)
    return r


def test_a_registry_every_merge_edits_reads_above_the_threshold(registry_repo: Path) -> None:
    block = _ok(scan_concurrency(registry_repo, None, None))
    assert block["merges"] == 12
    assert block["threshold"] == TOUCH_SHARE_THRESHOLD
    # registry.py 12/12; catalog.json 4/12 (with .gitattributes in the first merge too)
    assert block["files_above_threshold"] == 2
    assert block["max_touch_share"] == 1.0
    # merge 0: feature, registry, catalog, .gitattributes; then 2, 2, 3, 2, 2, 3, ...
    sizes = [4, 2, 2, 3, 2, 2, 3, 2, 2, 3, 2, 2]
    assert block["median_files_per_merge"] == float(statistics.median(sizes))
    assert block["files_touched"] == 12 + 3
    hot = block["hot_files"]
    assert [h["path"] for h in hot] == ["registry.py", "catalog.json"]
    assert hot[0] == {"path": "registry.py", "merges": 12, "touch_share": 1.0,
                      "generated": False, "fan_in": 12 + 1}  # twelve feature folders and "."
    assert hot[1]["generated"] is True and hot[1]["touch_share"] == round(4 / 12, 4)
    assert block["max_fan_in"] == 13 and block["fan_in_null_reason"] is None
    assert block["generated_files_touched"] == 1
    assert block["generated_files_above_threshold"] == 1
    assert block["generated_merge_share"] == round(4 / 12, 4)
    assert block["generated_diff_share"] == round(4 / sum(sizes), 4)
    assert block["informational"] is True and block["limits"] == list(LIMITS)
    assert block["window"] == "all-time" and block["since"] is None


def test_merges_on_disjoint_files_read_nothing_above_the_threshold(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(MIN_MERGES):
        _commit(r, {f"part{i}/a.py": "a\n", f"part{i}/b.py": "b\n"}, T0 + i * DAY)
    block = _ok(scan_concurrency(r, None, None))
    assert block["merges"] == MIN_MERGES
    assert block["files_above_threshold"] == 0
    assert block["max_touch_share"] == round(1 / MIN_MERGES, 4)
    assert block["median_files_per_merge"] == 2.0
    assert block["hot_files"] == [] and block["max_fan_in"] is None
    assert block["fan_in_null_reason"] == NONE_ABOVE_THRESHOLD
    assert block["generated_files_touched"] == 0 and block["generated_diff_share"] == 0.0


def test_a_merge_commit_counts_its_diff_against_the_first_parent(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(MIN_MERGES):
        _commit(r, {f"base{i}.txt": "x\n"}, T0 + i * DAY)
    _git(r, "checkout", "-q", "-b", "topic")
    _commit(r, {"topic/a.txt": "a\n"}, T0 + 20 * DAY)
    _commit(r, {"topic/b.txt": "b\n"}, T0 + 21 * DAY)
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "merge topic", "topic", at=T0 + 22 * DAY)
    block = _ok(scan_concurrency(r, None, None))
    assert block["merges"] == MIN_MERGES + 1  # the topic commits are not first-parent
    assert block["files_touched"] == MIN_MERGES + 2


def test_scope_counts_only_files_under_the_scope(registry_repo: Path) -> None:
    scope, _, _ = resolve_scope(registry_repo, ["feature1"])
    assert scope is not None
    block = scan_concurrency(registry_repo, scope, None)
    assert block == {"available": False, "reason": block["reason"]}
    assert block["reason"].startswith("short_history: ")  # one merge touched feature1


def test_this_repository_reads_the_generated_pair_above_the_threshold() -> None:
    """Over the 60 merges before FIXED_COMMIT (CI checks out full history), both files of
    the generated catalog pair are above the threshold and marked generated, and the median
    files per merge is the one git itself gives."""
    try:
        _git(TOOLKIT, "cat-file", "-e", f"{FIXED_COMMIT}^{{commit}}")
    except subprocess.CalledProcessError:
        pytest.fail(f"{FIXED_COMMIT} is not in this clone: the test needs full history")
    block = _ok(concurrency_block(TOOLKIT, None, None, rev=FIXED_COMMIT, merge_sample=60))
    assert block["merges"] == 60
    hot = {h["path"]: h for h in block["hot_files"]}
    for path in GENERATED_PAIR:
        assert path in hot, f"{path} is not above the touch-share threshold"
        assert hot[path]["generated"] is True
        assert hot[path]["touch_share"] >= TOUCH_SHARE_THRESHOLD
    assert block["generated_files_above_threshold"] >= 2
    # git log <commit> --first-parent -m -M -n60 --format=format:@ --name-only: files per merge
    out = _git(TOOLKIT, "-c", "core.quotepath=false", "log", FIXED_COMMIT, "--first-parent",
               "-m", "-M", "-n60", "--format=format:@", "--name-only")
    sizes = [len({line for line in rec.splitlines() if line.strip()}) for rec in out.split("@")[1:]]
    assert len(sizes) == 60
    assert block["median_files_per_merge"] == float(statistics.median(sizes))


def test_fewer_merges_than_the_minimum_read_short_history(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(MIN_MERGES - 1):
        _commit(r, {f"f{i}.txt": "x\n"}, T0 + i * DAY)
    block = scan_concurrency(r, None, None)
    assert block["available"] is False and block["reason"].startswith("short_history: ")
    reduced = counts_only_scans({"concurrency": block})["concurrency"]
    assert reduced == {"available": False, "reason": "short_history"}


def test_a_window_counts_back_from_head(registry_repo: Path) -> None:
    block = scan_concurrency(registry_repo, None, "1w")
    assert block["available"] is False and block["reason"].startswith("short_history: ")
    assert _ok(scan_concurrency(registry_repo, None, "1y"))["window"] == "1y"


def test_a_shallow_clone_reads_not_measured(registry_repo: Path, tmp_path: Path) -> None:
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "3", registry_repo.as_uri(), str(clone)],
                   check=True, capture_output=True, encoding="utf-8", errors="replace")
    block = scan_concurrency(clone, None, None)
    assert block["available"] is False and block["reason"].startswith("shallow_clone: ")
    reduced = counts_only_scans({"concurrency": block})["concurrency"]
    assert reduced == {"available": False, "reason": "shallow_clone"}


def test_not_a_repository_is_unavailable(tmp_path: Path) -> None:
    block = scan_concurrency(tmp_path, None, None)
    assert block["available"] is False and block["reason"].startswith("not_a_repo: ")


def test_counts_only_keeps_every_count_and_no_path(registry_repo: Path) -> None:
    block = _ok(scan_concurrency(registry_repo, None, None))
    reduced = counts_only_scans({"concurrency": block})["concurrency"]
    numeric = {k for k, v in block.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool) or v is None}
    assert numeric <= set(reduced), "a count was dropped from counts-only output"
    for key in numeric:
        assert reduced[key] == block[key]  # type: ignore[literal-required]
    assert reduced["window"] == "all-time"
    text = json.dumps(reduced)
    for leak in ("registry", "catalog", "feature", ".py", ".json", "/"):
        assert leak not in text, f"{leak!r} leaked into counts-only output"


def test_a_pooled_run_writes_the_same_block_as_jobs_1(registry_repo: Path) -> None:
    """--jobs runs the scans of a stage at once (lib/run/scan_pool.py, #1381): the headless
    document, and this block in it, read the same bytes under --jobs 4 as under --jobs 1."""
    from lib.run.signals import build_document

    one, four = (build_document(registry_repo, jobs=jobs)["scans"] for jobs in (1, 4))
    assert one["concurrency"]["available"] is True
    assert json.dumps(four, indent=2, default=str) == json.dumps(one, indent=2, default=str)


def test_registered_through_the_manifest_and_read_by_no_condition() -> None:
    spec = next(s for s in SCANS if s.key == "concurrency")
    assert spec.fn is scan_concurrency
    assert spec.reads == ("repo_root", "scope", "history_window")
    levels = (TOOLKIT / "skills/assess/levels/default.toml").read_text(encoding="utf-8")
    assert "concurrency" not in levels


def test_a_root_below_the_repository_top_reads_only_its_merges(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(MIN_MERGES):
        _commit(r, {f"sub/f{i}.txt": "x\n", "registry.py": f"N = {i}\n"}, T0 + i * DAY)
    for i in range(5):
        _commit(r, {f"other/g{i}.txt": "y\n"}, T0 + (20 + i) * DAY)
    block = _ok(scan_concurrency(r / "sub", None, None))
    assert block["merges"] == MIN_MERGES
    assert block["files_touched"] == MIN_MERGES  # registry.py lies outside sub/
    assert block["files_above_threshold"] == 0


def test_a_later_git_failure_reads_git_failed(registry_repo: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.history.concurrency as concurrency

    real = subprocess.run

    def refuse_check_attr(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "check-attr" in cmd:
            raise subprocess.CalledProcessError(128, cmd)
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(concurrency.subprocess, "run", refuse_check_attr)
    block = scan_concurrency(registry_repo, None, None)
    assert block["available"] is False and block["reason"].startswith("git_failed: ")
    reduced = counts_only_scans({"concurrency": block})["concurrency"]
    assert reduced == {"available": False, "reason": "git_failed"}


def test_fan_in_counts_folders_below_the_measured_root(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(12):
        _commit(r, {f"pkg/feature{i}/main.py": f"x = {i}\n", "pkg/registry.py": f"N = {i}\n"},
                T0 + i * DAY)
    at_top = _ok(scan_concurrency(r, None, None))
    below = _ok(scan_concurrency(r / "pkg", None, None))
    scope, _, _ = resolve_scope(r, ["pkg"])
    scoped = _ok(scan_concurrency(r, scope, None))
    assert at_top["max_fan_in"] == 1  # every other file is under pkg/
    assert below["max_fan_in"] == scoped["max_fan_in"] == 12


def test_a_path_with_a_quote_is_read_as_written(tmp_path: Path) -> None:
    """git C-quotes such a name in --name-only output even with core.quotepath=false; -z
    keeps it as written, so its linguist-generated attribute matches. The file goes in
    through the index only (core.protectNTFS off), since Windows cannot hold a '"' in a
    file name."""
    r = _init(tmp_path)
    (r / ".gitattributes").write_text('"gen/we\\"ird.json" linguist-generated\n', encoding="utf-8")
    name = 'gen/we"ird.json'
    for i in range(MIN_MERGES):
        blob = subprocess.run(["git", "-C", str(r), "hash-object", "-w", "--stdin"], input=f"{i}\n",
                              check=True, capture_output=True, encoding="utf-8",
                              errors="replace").stdout.strip()
        # Git for Windows refuses an NTFS-invalid name in the index unless told not to
        _git(r, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo",
             f"100644,{blob},{name}")
        if i == 0:
            _git(r, "add", ".gitattributes")
        # a plain commit, never add -A, which would stage the index-only file's removal
        _git(r, "commit", "-q", "-m", f"regenerate {i}", at=T0 + i * DAY)
    block = _ok(scan_concurrency(r, None, None))
    hot = {h["path"]: h for h in block["hot_files"]}
    assert name in hot, sorted(hot)
    assert hot[name]["generated"] is True and hot[name]["touch_share"] == 1.0


def test_a_configured_diff_merges_format_does_not_change_the_numbers(tmp_path: Path) -> None:
    r = _init(tmp_path)
    for i in range(MIN_MERGES):
        _commit(r, {f"base{i}.txt": "x\n"}, T0 + i * DAY)
    _git(r, "checkout", "-q", "-b", "topic")
    _commit(r, {"topic/a.txt": "a\n"}, T0 + 20 * DAY)
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "merge topic", "topic", at=T0 + 22 * DAY)
    plain = _ok(scan_concurrency(r, None, None))
    _git(r, "config", "log.diffMerges", "combined")
    assert _ok(scan_concurrency(r, None, None)) == plain
    assert plain["files_touched"] == MIN_MERGES + 1
