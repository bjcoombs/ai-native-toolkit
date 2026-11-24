"""The `--shard K/N` plugin (tests/pytest_shard.py) drops no test and runs none twice.

The Windows workflow splits `skills/assess` into four legs and the ghsync and
ghreport suites into two. Each leg keeps one shard, so a shard plan that left a
file out would drop it from Windows CI with every leg green. These tests pin
the guard: over real collections, the N shards are disjoint and their union is
the full collection.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest_shard  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"


def _sizes(paths: list[Path]) -> dict[str, float]:
    return {p.relative_to(REPO).as_posix(): float(p.stat().st_size) for p in paths}


def _assert_partition(files: dict[str, float], shards: list[list[str]]) -> None:
    flat = [f for shard in shards for f in shard]
    assert len(flat) == len(set(flat)), "a file sits in two shards"
    assert set(flat) == set(files), "the shards do not cover every file"


@pytest.mark.parametrize("value, expected", [("1/1", (1, 1)), ("2/4", (2, 4)), ("4/4", (4, 4))])
def test_parse_shard_accepts_k_of_n(value: str, expected: tuple[int, int]) -> None:
    assert pytest_shard.parse_shard(value) == expected


@pytest.mark.parametrize("value", ["0/4", "5/4", "1/0", "1", "a/b", "1/2/3", ""])
def test_parse_shard_rejects_out_of_range(value: str) -> None:
    with pytest.raises(pytest.UsageError):
        pytest_shard.parse_shard(value)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 20])
def test_plan_is_a_deterministic_partition(n: int) -> None:
    files = {f"tests/test_{i:02d}.py": float((i * 37) % 11 + 1) for i in range(13)}
    shards = pytest_shard.plan_shards(files, n)
    assert len(shards) == n
    _assert_partition(files, shards)
    assert pytest_shard.plan_shards(dict(reversed(list(files.items()))), n) == shards


def test_plan_balances_by_weight() -> None:
    # Heaviest first, each to the lightest shard: the big file sits alone.
    files = {"b": 3.0, "a": 10.0, "d": 3.0, "c": 3.0}
    assert pytest_shard.plan_shards(files, 2) == [["a"], ["b", "c", "d"]]


def test_check_partition_rejects_overlap_and_gaps() -> None:
    with pytest.raises(pytest.UsageError, match="shards 1 and 2"):
        pytest_shard.check_partition(["a", "b"], [["a"], ["a", "b"]])
    with pytest.raises(pytest.UsageError, match="missing"):
        pytest_shard.check_partition(["a", "b"], [["a"], []])


def test_measured_weights_fall_back_to_the_median(tmp_path: Path) -> None:
    files = {k: tmp_path / k for k in ("a", "b", "c", "d")}
    weights = pytest_shard.file_weights(files, {"a": 1.0, "b": 3.0, "c": 5.0})
    assert weights == {"a": 1.0, "b": 3.0, "c": 5.0, "d": 3.0}


@pytest.mark.parametrize(
    "files, n",
    [
        (sorted((REPO / "skills" / "assess" / "tests").glob("test_*.py")), 4),
        (sorted(TESTS.glob("test_ghsync*.py")) + sorted(TESTS.glob("test_ghreport*.py")), 2),
    ],
    ids=["assess-4", "ghsync-2"],
)
def test_real_suites_split_into_non_empty_disjoint_shards(files: list[Path], n: int) -> None:
    weights = _sizes(files)
    assert len(weights) > n
    shards = pytest_shard.plan_shards(weights, n)
    _assert_partition(weights, shards)
    assert all(shards), "a shard with no files would run zero tests"


def _collect(root: Path, *args: str) -> list[str]:
    env = dict(os.environ, PYTHONPATH=str(TESTS))
    env.pop("PYTEST_XDIST_WORKER", None)
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-p", "pytest_shard", "--rootdir", str(root), "-c", str(root / "pytest.ini"), *args, str(root)],
        cwd=root, env=env, capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return [line for line in out.stdout.splitlines() if "::" in line]


def test_shard_collections_union_to_the_full_collection(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for i in range(9):
        body = "".join(f"def test_{j}():\n    pass\n\n" for j in range(i + 1))
        (tmp_path / f"test_mod{i}.py").write_text(body + "#" * (i * 50) + "\n", encoding="utf-8")
    full = _collect(tmp_path)
    assert len(full) == 45
    n = 4
    shards = [_collect(tmp_path, "--shard", f"{k}/{n}") for k in range(1, n + 1)]
    flat = [node for shard in shards for node in shard]
    assert len(flat) == len(set(flat)), "a test ran in two shards"
    assert sorted(flat) == sorted(full), "the shards dropped or added a test"
    for shard in shards:
        assert shard, "an empty shard"
        files = {node.split("::")[0] for node in shard}
        for other in shards:
            if other is not shard:
                assert not files & {node.split("::")[0] for node in other}, "a file split across shards"
