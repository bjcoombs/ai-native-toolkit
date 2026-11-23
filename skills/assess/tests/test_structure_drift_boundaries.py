"""structure_drift Tier 0 ``boundary_patterns``: matched declarations less the catch-all (#785).

A CODEOWNERS whose only pattern is ``*`` names one owner for the whole tree and no
module boundary, so Layer 4 level 1 reads ``boundary_patterns`` instead of
``matched_patterns``. An architecture-doc reference passes the same module-source
predicate, so one naming only ``CONTRIBUTING.md`` or ``.github/`` paths counts 0 (#874).
Kept apart from ``test_structure_drift.py``, which sits near its
file-size ceiling.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib.context_blocks import structure_drift_tier_0
from lib.docs.ownership_parser import is_boundary_pattern
from lib.keyhole.structure_drift import detect_path_existence_drift

_ARCH = "# Architecture\n\nThe entry point is `src/a.py`.\n"
_ARCH_NON_MODULE = ("# Architecture\n\nRead `CONTRIBUTING.md` first; CI runs "
                    "`.github/workflows/ci.yml`.\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace",
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in {"src/a.py": "x = 1\n", **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    return root


@pytest.mark.parametrize(("files", "expected"), [
    pytest.param({".github/CODEOWNERS": "* @x\n"}, 0, id="star"),
    pytest.param({"CODEOWNERS": "/** @x\n"}, 0, id="slash-double-star"),
    pytest.param({"CODEOWNERS": "/* @x\n** @y\n"}, 0, id="slash-star-and-double-star"),
    pytest.param({"CODEOWNERS": "**/* @x\n/**/* @y\n"}, 0, id="double-star-slash-star"),
    pytest.param({"CODEOWNERS": "*.py @x\n"}, 0, id="extension-glob"),
    pytest.param({"CODEOWNERS": "*.py @x\n/src/** @team\n"}, 1, id="extension-glob-plus-src"),
    pytest.param({"CODEOWNERS": "src/*.py @team\n"}, 1, id="extension-glob-under-a-path"),
    pytest.param({".github/CODEOWNERS": "* @org\n/.github/ @org/devops\n",
                  ".github/workflows/ci.yml": "on: push\n"}, 0, id="star-plus-dot-github"),
    pytest.param({"CODEOWNERS": "/.github/ @x\n.gitlab/ @y\n", ".github/scripts/s.py": "x = 1\n",
                  ".gitlab/ci.yml": "a: 1\n"}, 0, id="dot-directories"),
    pytest.param({"CODEOWNERS": "README.md @x\nLICENSE @x\nCODEOWNERS @x\nsetup.py @x\n",
                  "README.md": "# r\n", "LICENSE": "x\n", "setup.py": "x = 1\n"}, 0,
                 id="root-files"),
    pytest.param({"CODEOWNERS": "* @x\ndocs/ @writers\n", "docs/guide.md": "# g\n"}, 0,
                 id="docs-only-path"),
    pytest.param({"CODEOWNERS": "src/a.py @x\n"}, 1, id="one-source-file-in-a-module"),
    pytest.param({".github/CODEOWNERS": "* @x\nsrc/ @team\n"}, 1, id="star-plus-src"),
    pytest.param({".github/CODEOWNERS": "src/ @team\n"}, 1, id="src-only"),
    pytest.param({"ARCHITECTURE.md": _ARCH}, 1, id="architecture-one-path"),
    pytest.param({".github/CODEOWNERS": "* @x\n", "ARCHITECTURE.md": _ARCH}, 1,
                 id="star-plus-architecture"),
    pytest.param({".github/CODEOWNERS": "* @x\nghost/ @team\n"}, 0, id="unmatched-path-not-counted"),
    # Architecture-doc references pass the same module-source predicate (#874).
    pytest.param({"ARCHITECTURE.md": _ARCH_NON_MODULE, "CONTRIBUTING.md": "# c\n",
                  ".github/workflows/ci.yml": "on: push\n"}, 0,
                 id="architecture-names-only-contributing-and-ci"),
    pytest.param({"ARCHITECTURE.md": "# Architecture\n\nThe package lives in `src/pkg/`.\n",
                  "src/pkg/core.py": "x = 1\n"}, 1, id="architecture-names-src-pkg"),
    pytest.param({"ARCHITECTURE.md": "# Architecture\n\nSee `docs/guide.md` and `docs/`.\n",
                  "docs/guide.md": "# g\n"}, 0, id="architecture-names-only-docs"),
    pytest.param({"ARCHITECTURE.md": "# A\n\nCI scripts in `.github/scripts/`.\n",
                  ".github/scripts/s.py": "x = 1\n"}, 0, id="architecture-names-dot-directory-source"),
    pytest.param({"ARCHITECTURE.md": _ARCH_NON_MODULE + "\n## Core\n\nOwns `src/`.\n",
                  "CONTRIBUTING.md": "# c\n", ".github/workflows/ci.yml": "on: push\n"}, 1,
                 id="architecture-mixed-counts-only-module-ref"),
])
def test_boundary_patterns_excludes_catch_all(tmp_path: Path, files: dict[str, str], expected: int) -> None:
    repo = _repo(tmp_path / "r", files)
    tier_0 = detect_path_existence_drift(repo)
    assert tier_0["boundary_patterns"] == expected
    # matched_patterns keeps its meaning: the catch-all still matches files.
    assert tier_0["matched_patterns"] >= expected
    block = structure_drift_tier_0(repo)
    assert block is not None
    assert block["tier_0"]["boundary_patterns"] == expected


def test_no_ownership_map_omits_the_block(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", {})
    assert detect_path_existence_drift(repo)["boundary_patterns"] == 0
    assert structure_drift_tier_0(repo) is None


@pytest.mark.parametrize(("pattern", "files", "expected"), [
    ("src/", {"src/a.py"}, True),
    ("**/tests/", {"pkg/tests/t.py"}, True),
    ("src/", {"src/README.md"}, False),
    ("/.github/", {".github/scripts/s.py"}, False),
    ("tools/", {"tools/.hidden/x.py"}, False),
    ("app.py", {"app.py"}, False),
    ("*.py", {"src/a.py"}, False),
    ("src/", set(), False),
])
def test_is_boundary_pattern(pattern: str, files: set[str], expected: bool) -> None:
    assert is_boundary_pattern(pattern, {Path(f) for f in files}) is expected
