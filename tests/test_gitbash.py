"""The Git Bash helpers and the jq wrapper the bash skill scripts define (#861).

- `gitbash.rmtree` removes a tree holding a read-only file without the
  `onerror=` deprecation warning Python 3.12 raises.
- ghsync.sh and ghreport.sh wrap jq with `--binary` under Git Bash only when the
  jq on PATH accepts the flag, since a POSIX jq 1.6 from MSYS2 or Cygwin rejects it.
"""
import os
import re
import stat
import subprocess
import warnings
from pathlib import Path

import pytest

from gitbash import BASH, base_env, rmtree, write_script

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = (REPO / "skills" / "ghsync" / "scripts" / "ghsync.sh",
           REPO / "skills" / "ghreport" / "scripts" / "ghreport.sh")
WRAPPER = re.compile(r'^case "\$\{OSTYPE:-\}" in\n.*?^esac$', re.MULTILINE | re.DOTALL)
# A jq stub: accepts or rejects --binary as a native jq.exe or a POSIX jq 1.6 would.
JQ_STUB = """#!/usr/bin/env bash
for a in "$@"; do
    if [ "$a" = --binary ] && [ -z "${JQ_ACCEPTS_BINARY:-}" ]; then
        echo "jq: Unknown option: --binary" >&2
        exit 2
    fi
done
exit 0
"""


def test_rmtree_removes_a_read_only_file_without_a_deprecation_warning(tmp_path: Path) -> None:
    tree = tmp_path / "bare.git" / "objects"
    tree.mkdir(parents=True)
    obj = tree / "ab"
    obj.write_text("x", encoding="utf-8")
    os.chmod(obj, stat.S_IREAD)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        rmtree(tmp_path / "bare.git")
    assert not (tmp_path / "bare.git").exists()


def _wrapper(script: Path) -> str:
    found = WRAPPER.search(script.read_text(encoding="utf-8"))
    assert found, f"{script.name}: the OSTYPE jq wrapper block is missing"
    return found.group(0)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize(("ostype", "accepts", "expected"), [
    ("msys", True, "function"),
    ("msys", False, "file"),
    ("cygwin", False, "file"),
    ("linux-gnu", True, "file"),
])
def test_jq_is_wrapped_only_when_it_accepts_binary(tmp_path: Path, script: Path, ostype: str,
                                                  accepts: bool, expected: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_script(bin_dir / "jq", JQ_STUB)
    env = {**base_env(), "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])}
    if accepts:
        env["JQ_ACCEPTS_BINARY"] = "1"
    probe = f"OSTYPE={ostype}\n{_wrapper(script)}\ntype -t jq\n"
    out = subprocess.run([BASH, "-c", probe], env=env, capture_output=True,
                         encoding="utf-8", errors="replace", check=True)
    assert out.stdout.strip() == expected
