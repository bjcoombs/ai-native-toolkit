"""The typed shapes of the blocks a default criterion reads match what their builders write (#793).

``lib.counts_only.block_shapes`` gives ``agent_ops``, ``coverage_gate``, ``instruction_claims``,
``promissory_markers``, ``review_reality`` and ``structure_drift`` a root in
``counts_only_schema.ROOTS``, so ``level_signals.team_path_ok`` walks every segment
of a ``--levels`` path under them. A shape that names a key its builder does not
write would accept a path that always evaluates unknown; one that misses a key would
reject a valid path. These tests run each builder on a small repository and check
every key and leaf type against the shape, and check the fleet golden's blocks too.
"""
from __future__ import annotations

import json
import os
import subprocess
import types
import typing
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import pytest

import lib.counts_only.block_shapes as bs
from lib.agents.agent_ops import scan_agent_ops
from lib.run.context_blocks import _structure_drift_block
from lib.counts_only.counts_only_schema import ROOTS
from lib.counts_only.content_free import HASH_PREFIX, counts_only
from lib.testing.coverage_gate import detect_coverage_gate
from lib.run.headless_layers import structure_drift as headless_structure_drift
from lib.instructions.instruction_claims import scan_instruction_claims
from lib.scoring.level_signals import _ROOTS, load_default_levels
from lib.scoring.levels import LevelsError, load_levels
from lib.scoring.level_signals import signal_path_validator
from lib.keyhole.promissory_markers import scan_promissory_markers
from lib.hosting.review_reality import summarize

FLEET_GOLDEN = Path(__file__).parent / "fixtures" / "fleet_golden" / "repos.jsonl"
SHAPES: dict[str, Any] = {
    "agent_ops": bs.AgentOpsBlock,
    "coverage_gate": bs.CoverageGateBlock,
    "instruction_claims": bs.InstructionClaimsBlock,
    "promissory_markers": bs.PromissoryMarkersBlock,
    "review_reality": bs.ReviewRealityBlock,
    "structure_drift": bs.StructureDriftBlock,
}
_UNIONS = (Union, types.UnionType)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_SCALARS: dict[Any, Any] = {type(None): lambda v: v is None, bool: lambda v: isinstance(v, bool),
                            int: _is_int, float: _is_number, str: _is_str}


def _problems(value: Any, tp: Any, where: str, counts_only: bool = False) -> list[str]:
    """Where ``value`` does not fit ``tp``; a missing NotRequired key is fine.

    ``counts_only`` reads a reduced document: a dropped key is fine, and a list of
    strings may be its length."""
    origin = get_origin(tp)
    if origin is typing.NotRequired or origin is typing.Required:
        return _problems(value, get_args(tp)[0], where, counts_only)
    if origin in _UNIONS:
        arms = [_problems(value, arm, where, counts_only) for arm in get_args(tp)]
        return [] if any(not a for a in arms) else min(arms, key=len)
    if tp in _SCALARS or origin is Literal:
        ok = _SCALARS.get(tp, _is_str)(value)
        return [] if ok else [f"{where}: {value!r} is not {getattr(tp, '__name__', tp)}"]
    if origin is list:
        if counts_only and isinstance(value, int) and get_args(tp)[0] is str:
            return []
        if not isinstance(value, list):
            return [f"{where}: {value!r} is not a list"]
        return [p for i, v in enumerate(value)
                for p in _problems(v, get_args(tp)[0], f"{where}[{i}]", counts_only)]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: {value!r} is not a map"]
        return [p for k, v in value.items()
                for p in _problems(v, get_args(tp)[1], f"{where}.{k}", counts_only)]
    if typing.is_typeddict(tp):
        return _record_problems(value, tp, where, counts_only)
    raise AssertionError(f"{where}: unhandled annotation {tp!r}")


def _record_problems(value: Any, tp: Any, where: str, counts_only: bool) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where}: {value!r} is not an object"]
    hints = get_type_hints(tp)
    out = [f"{where}.{k}: written by the builder, not in {tp.__name__}"
           for k in value if k not in hints]
    if not counts_only:
        # Read NotRequired from the hints: the postponed annotations hide it
        # from ``__required_keys__``.
        extras = get_type_hints(tp, include_extras=True)
        out += [f"{where}.{k}: in {tp.__name__}, not written by the builder"
                for k in hints if k not in value
                and get_origin(extras[k]) is not typing.NotRequired]
    return out + [p for k, v in value.items() if k in hints
                  for p in _problems(v, hints[k], f"{where}.{k}", counts_only)]


def _is_marker(block: Any) -> bool:
    return isinstance(block, dict) and block.get("available") is False and set(block) <= {
        "available", "reason"}


def _git(repo: Path, *args: str, day: int = 1) -> None:
    date = f"2020-01-{day:02d}T00:00:00+00:00"  # one commit a day, so markers age
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@example.com", "-c", "user.name=A",
                    *args], check=True, capture_output=True, encoding="utf-8", errors="replace",
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository that populates every list and map the six shapes declare."""
    root = tmp_path_factory.mktemp("shapes") / "repo"
    files = {
        ".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(make:*)"]},
                                             "hooks": {"PreToolUse": []}, "sandbox": {}}),
        ".claude/hooks/check.sh": "#!/bin/sh\n",
        ".claude/routines/nightly.md": "# nightly\n",
        "pyproject.toml": "[tool.coverage.report]\nfail_under = 90\n",
        "CLAUDE.md": "# Agents\n\nNode 20.11.0 is pinned in `.nvmrc`.\n",
        ".nvmrc": "18.19.0\n",
        "CODEOWNERS": "/src/ @team\n/gone/ @team\n",
        "src/app.py": "def f():\n    # TODO: remove after the migration\n    return 1  # noqa: E501\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    for i in range(6):  # age the markers past the stale threshold
        (root / "src" / "app.py").write_text(files["src/app.py"] + f"X = {i}\n", encoding="utf-8")
        _git(root, "commit", "-qam", f"touch {i}", day=i + 2)
    return root


def _built(repo: Path) -> dict[str, list[Any]]:
    pr = {"author": {"login": "a"}, "mergedAt": "2026-01-01T00:00:00Z",
          "reviews": [{"author": {"login": "b"}, "state": "APPROVED"}], "comments": []}
    tier_1 = {"available": True, "human_static_agree_count": 1}
    return {
        "agent_ops": [scan_agent_ops(repo)],
        "coverage_gate": [detect_coverage_gate(repo)],
        "instruction_claims": [scan_instruction_claims(repo, ["CLAUDE.md"])],
        "promissory_markers": [scan_promissory_markers(repo, stale_touches=1).summary()],
        "review_reality": [summarize([pr] * 5, True)],
        "structure_drift": [_structure_drift_block(repo, tier_1),
                            _structure_drift_block(repo, {"available": False}),
                            headless_structure_drift(repo)],
    }


def test_builders_write_what_the_shapes_declare(repo: Path) -> None:
    built = _built(repo)
    problems = [p for key, blocks in built.items() for block in blocks
                for p in _problems(block, SHAPES[key], key)]
    assert problems == []
    # The fixture reaches the lists and maps, so their item shapes are checked too.
    assert built["agent_ops"][0]["settings"] and built["coverage_gate"][0]["gates"]
    assert built["instruction_claims"][0]["failures"]
    promissory = built["promissory_markers"][0]
    assert promissory["families"] and promissory["stale_by_file"] and promissory["top_offenders"]
    assert built["structure_drift"][0]["tier_0"]["empty_ownership_patterns"]


def test_fleet_golden_blocks_fit_the_shapes() -> None:
    """The counts-only fleet golden: every key it keeps is a field of the shape."""
    problems: list[str] = []
    for n, line in enumerate(FLEET_GOLDEN.read_text(encoding="utf-8").splitlines()):
        scans = json.loads(line)["scans"]
        for key, tp in SHAPES.items():
            block = scans.get(key)
            if block is not None and not _is_marker(block):
                problems += _problems(block, tp, f"row {n} {key}", counts_only=True)
    assert problems == []


def test_every_block_a_default_criterion_reads_is_typed() -> None:
    """No default signal path is accepted only because its block is open."""
    untyped = sorted({c["signal"] for row in load_default_levels()["rows"]
                      for lvl in row["levels"] for c in lvl["criteria"]
                      if not any(".".join(c["signal"].split(".")[:cut]) in _ROOTS
                                 for cut in range(1, len(c["signal"].split(".")) + 1))})
    assert untyped == []


def test_the_shapes_are_typed_roots() -> None:
    assert {(key, tp) for key, tp in SHAPES.items()} <= set(ROOTS)


@pytest.mark.parametrize("signal", [
    "agent_ops.summary.permissions_encodd",
    "coverage_gate.enforcd",
    "promissory_markers.families.suppression.totl",
    "structure_drift.tier_0.matched",
    "review_reality.reviewd_share",
    "instruction_claims.faild",
])
def test_a_misspelt_sub_path_fails_at_load(tmp_path: Path, signal: str) -> None:
    """The stated effect of #793: this used to load and evaluate unknown."""
    model = tmp_path / "levels.toml"
    model.write_text(
        'version = "fixture-t"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "team"\n'
        'aggregation = "min"\n[[rows.levels]]\nlevel = 1\n'
        f'criteria = [{{ signal = "{signal}", op = "eq", value = true }}]\n', encoding="utf-8")
    with pytest.raises(LevelsError, match=f"team:{signal}"):
        load_levels(model, signal_path_validator())


def test_counts_only_keeps_the_newly_typed_integer_and_counts_the_families(repo: Path) -> None:
    """The stated counts-only effect of #793: ``max_survived`` became a known field name,
    so it now survives reduction; the per-file ``families`` list stays a count."""
    block = scan_promissory_markers(repo, stale_touches=1).summary()
    for hashed in (False, True):
        out = counts_only({"promissory_markers": block}, hash_paths=hashed, salt="s")
        rows = out["promissory_markers"]["stale_by_file"]
        assert rows and all(set(r) == {"count", "families", "max_survived"} for r in rows.values())
        assert all(isinstance(r["families"], int) and r["max_survived"] >= 1 for r in rows.values())
        assert all(k.startswith(HASH_PREFIX) for k in rows) is hashed


def test_hashed_counts_only_promissory_block_carries_no_path(repo: Path) -> None:
    """``stale_by_file`` is path-keyed (``PATH_KEYED_MAPS``): under ``--hash-paths`` no
    repository path survives, as a key or a value, with ``max_survived`` now kept."""
    block = scan_promissory_markers(repo, stale_touches=1).summary()
    assert block["stale_by_file"] and block["top_offenders"]
    text = json.dumps(counts_only({"promissory_markers": block}, hash_paths=True, salt="s"))
    paths = {"src/app.py", "src", "app.py"} | set(block["stale_by_file"])
    assert [p for p in paths if p in text] == []
    assert "max_survived" in text
