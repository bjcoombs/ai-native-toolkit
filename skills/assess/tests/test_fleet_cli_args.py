"""lib/fleet/fleet_cli_args.py: the fleet.py flags and their usage rules (#1214)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.fleet.fleet_cli_args import LATEST, parse_args


def test_bare_compare_is_latest_and_root_is_kept() -> None:
    args = parse_args(["root", "--out", "o", "--compare"])
    assert (args.root, args.compare) == ("root", LATEST)


@pytest.mark.parametrize("argv", [
    ["--out", "o"],  # neither ROOT nor --from-list
    ["root", "--out", "o", "--full", "--hash-paths"],
    ["root", "--out", "o", "--jobs", "0"],
    ["root", "--out", "o", "--platform-jobs", "2"],  # needs --platform
    ["root", "--out", "o", "--monorepo-units"],  # needs --levels
])
def test_usage_rules_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2


def test_compare_that_swallowed_root_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--out", "o", "--compare", str(tmp_path)])
    assert exc.value.code == 2
