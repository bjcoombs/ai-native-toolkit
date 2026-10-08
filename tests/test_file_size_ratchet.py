"""File-size ratchet: Python files under the configured roots may shrink, not grow.

Guards the accretion tendency named in CLAUDE.md's North star: an agent
appends feature after feature and nothing in that loop asks for a split, so
files only grow. Every Python file under the roots in `.file-size-ratchet.toml`
gets `default_limit` lines; a file already over it is allowlisted with its
line count as its ceiling. The failure messages point at splitting first and
at the allowlist only for justified growth.

The ceiling also follows a file down: once an allowlisted file sits more than
the shrink slack below its ceiling, the test fails until the ceiling is
lowered, so a refactor's savings cannot be silently regrown.
"""

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG_NAME = ".file-size-ratchet.toml"
CONFIG = REPO / CONFIG_NAME


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    """Parse the ratchet config.

    Keys: `default_limit`, `roots`, `shrink_slack_lines`,
    `shrink_slack_percent` and `[ceilings]`.
    """
    with path.open("rb") as fh:
        return tomllib.load(fh)


def python_files(repo: Path, roots: list[str]) -> list[str]:
    """Tracked plus untracked-but-not-ignored .py files under `roots`.

    Untracked files are included so a new file fails locally before commit;
    ignored ones (virtualenvs, caches) are not.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "--", *(f"{root}/*.py" for root in roots)],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    return sorted({p for p in out.splitlines() if (repo / p).is_file()})


def count_lines(path: Path) -> int:
    """Line count as `wc -l` reports it for a file ending in a newline."""
    return len(path.read_text(encoding="utf-8").splitlines())


def shrink_slack(ceiling: int, config: dict[str, Any]) -> int:
    """Lines a file may sit below its ceiling before the ceiling must follow it.

    `max(shrink_slack_lines, shrink_slack_percent of the ceiling)`, rounded
    down; `.file-size-ratchet.toml` states why.
    """
    lines: int = config["shrink_slack_lines"]
    percent: int = config["shrink_slack_percent"]
    return max(lines, ceiling * percent // 100)


def ratchet_violations(
    counts: dict[str, int], config: dict[str, Any]
) -> list[str]:
    """Every breach of the ratchet, each as an instruction a contributor can act on."""
    limit = config["default_limit"]
    ceilings = config.get("ceilings", {})
    problems: list[str] = []
    for path, lines in sorted(counts.items()):
        entry = ceilings.get(path)
        if entry is None:
            if lines > limit:
                problems.append(
                    f"{path} is {lines} lines, over the default limit of {limit}. "
                    "Split it: move a cohesive group of functions or tests into "
                    "its own module. Only if the size is justified, add it to "
                    f"{CONFIG_NAME} with `lines = {lines}` and a one-line `reason`."
                )
        elif lines > entry["lines"]:
            problems.append(
                f"{path} grew to {lines} lines, past its ceiling of "
                f"{entry['lines']}. Split it, or, if growth is justified, raise "
                f"its ceiling in {CONFIG_NAME} with a one-line reason."
            )
        elif lines <= limit:
            problems.append(
                f"{path} is now {lines} lines, within the default limit of "
                f"{limit}. Delete its [ceilings] entry from {CONFIG_NAME} so the "
                "default applies and the file cannot regrow past it."
            )
        elif entry["lines"] - lines > (slack := shrink_slack(entry["lines"], config)):
            problems.append(
                f"{path} is {lines} lines, {entry['lines'] - lines} below its "
                f"ceiling of {entry['lines']} (slack {slack}). Lower its ceiling "
                f"in {CONFIG_NAME} to {lines}, so the lines it shed cannot "
                "silently regrow."
            )
    for path in sorted(set(ceilings) - set(counts)):
        problems.append(
            f"{path} has a [ceilings] entry in {CONFIG_NAME} but is not a "
            "Python file under its `roots` (it was deleted, renamed or the path "
            "is mistyped). Fix the path or delete the entry."
        )
    return problems


def test_config_entries_are_well_formed() -> None:
    config = load_config()
    assert type(config.get("default_limit")) is int and config["default_limit"] > 0
    for key in ("shrink_slack_lines", "shrink_slack_percent"):
        assert type(config.get(key)) is int and config[key] >= 0, (
            f"{CONFIG_NAME} must set a non-negative integer `{key}`"
        )
    assert config.get("roots"), f"{CONFIG_NAME} must list the roots it covers"
    bad = [
        path for path, entry in config.get("ceilings", {}).items()
        # `type(...) is int`, not isinstance: bool is an int subclass.
        if not isinstance(entry, dict)
        or type(entry.get("lines")) is not int
        or not str(entry.get("reason", "")).strip()
        or set(entry) - {"lines", "reason"}
    ]
    assert not bad, (
        f"Each [ceilings] entry in {CONFIG_NAME} needs an integer `lines` and a "
        f"non-empty one-line `reason`, nothing else: {bad}"
    )


def test_python_files_stay_within_their_size_limit() -> None:
    config = load_config()
    files = python_files(REPO, config["roots"])
    assert files, f"no Python files found under {config['roots']}"
    counts = {path: count_lines(REPO / path) for path in files}
    problems = ratchet_violations(counts, config)
    assert not problems, "File-size ratchet:\n- " + "\n- ".join(problems)


# --- The rules themselves, on synthetic input --------------------------------

SYNTHETIC: dict[str, Any] = {
    "default_limit": 100,
    "shrink_slack_lines": 20,
    "shrink_slack_percent": 2,
    "ceilings": {
        "big.py": {"lines": 150, "reason": "pre-existing"},
        "huge.py": {"lines": 2000, "reason": "pre-existing"},
    },
}


@pytest.mark.parametrize(
    ("counts", "expected_fragment"),
    [
        ({"new.py": 101}, "over the default limit of 100. Split it"),
        ({"big.py": 151}, "past its ceiling of 150. Split it, or"),
        ({"big.py": 100}, "Delete its [ceilings] entry"),
        ({"big.py": 129},
         "Lower its ceiling in .file-size-ratchet.toml to 129, so"),
        ({"huge.py": 1959},
         "Lower its ceiling in .file-size-ratchet.toml to 1959, so"),
        ({}, "is not a Python file under its `roots`"),
    ],
    ids=["over-default", "allowlisted-grew", "allowlisted-shrank-under-default",
         "beyond-line-slack", "beyond-percent-slack", "allowlisted-file-gone"],
)
def test_ratchet_fails_with_an_actionable_message(
    counts: dict[str, int], expected_fragment: str
) -> None:
    problems = ratchet_violations(counts, SYNTHETIC)
    assert any(expected_fragment in p for p in problems), problems


@pytest.mark.parametrize(
    "counts",
    [
        {"small.py": 100, "big.py": 150, "huge.py": 2000},  # at limit / ceilings
        # exactly the 20-line floor of slack below its ceiling
        {"big.py": 130, "huge.py": 2000},
        # exactly 2% (40 lines) below: the percent beats the line floor
        {"big.py": 150, "huge.py": 1960},
    ],
    ids=["at-limits", "within-line-slack", "within-percent-slack"],
)
def test_ratchet_passes_within_limits(counts: dict[str, int]) -> None:
    assert ratchet_violations(counts, SYNTHETIC) == []


def test_assess_layer_scorer_recognises_the_ratchet_config() -> None:
    """/assess credits this ratchet as a Layer 3 file-size limit by its filename.

    The scorer finds linter config by name, so renaming the config without
    updating the scorer would silently drop the credit.
    """
    scorer = (REPO / "agents" / "assess-layer-scorer.md").read_text(encoding="utf-8")
    assert CONFIG_NAME in scorer
