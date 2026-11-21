"""Every ``--platform`` read names its target in argv (from #737's review, issue #715).

``lib/platform_cli.py`` caches a response under its argv alone, with no working
directory, so a call that relies on the cwd's git remote would be answered from
another repository's cache entry. This runs every scan in ``PLATFORM_SCANS``
through the headless runner with recording ``gh`` and ``glab`` stubs, once for
a GitHub remote and once for a GitLab remote. It asserts that every recorded argv
names its target (``--repo``, ``repos/<slug>``, ``users/<login>``, ``--hostname``)
and that the deep reads happened, so the check cannot pass on an empty log.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import signals  # noqa: E402

RECORDED = Path(__file__).resolve().parent / "fixtures" / "platform" / "github_merged_prs.json"

# Each call is logged as its arguments separated by \x1f, one call per line, and
# answered by the first case that matches the joined arguments.
FAKE_GH = r"""#!/bin/sh
printf '%s\037' gh "$@" >> "$FAKE_DIR/log"; echo >> "$FAKE_DIR/log"
all="$*"
case "$all" in
  "auth status"*) exit 0 ;;
  *"--json mergedAt") cat "$FAKE_DIR/light.json"; exit 0 ;;
  "pr list"*) cat "$FAKE_DIR/prs.json"; exit 0 ;;
  "api users/claude%5Bbot%5D"|"api users/coderabbitai%5Bbot%5D") echo '{"type":"Bot"}'; exit 0 ;;
  "api repos/acme/widget") echo '{"default_branch":"main"}'; exit 0 ;;
  "api repos/acme/widget/rules/branches/main") echo '[]'; exit 0 ;;
  "api repos/acme/widget/branches/main/protection") echo 'gh: Branch not protected (HTTP 404)' >&2; exit 1 ;;
esac
echo "gh: Not Found (HTTP 404)" >&2; exit 1
"""

FAKE_GLAB = r"""#!/bin/sh
printf '%s\037' glab "$@" >> "$FAKE_DIR/log"; echo >> "$FAKE_DIR/log"
case "$*" in
  "auth status"*) exit 0 ;;
  *"merge_requests?state=merged"*) cat "$FAKE_DIR/mrs.json"; exit 0 ;;
  *"/approvals") echo '{"approved_by":[{"user":{"username":"bob"}}]}'; exit 0 ;;
  *"approval_rules"*) echo '[{"rule_type":"regular","approvals_required":1,"applies_to_all_protected_branches":true}]'; exit 0 ;;
  *"protected_branches"*) echo '[{"name":"main"}]'; exit 0 ;;
  *"projects/grp%2Fapp") echo '{"default_branch":"main"}'; exit 0 ;;
esac
echo "glab: 404 Not Found (HTTP 404)" >&2; exit 1
"""


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir, data = tmp_path / "bin", tmp_path / "data"
    bindir.mkdir()
    data.mkdir()
    for name, body in (("gh", FAKE_GH), ("glab", FAKE_GLAB)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    prs = json.loads(RECORDED.read_text(encoding="utf-8"))
    (data / "prs.json").write_text(json.dumps(prs))
    (data / "light.json").write_text(json.dumps([{"mergedAt": pr["mergedAt"]} for pr in prs]))
    (data / "mrs.json").write_text(json.dumps([
        {"iid": i, "author": {"username": "alice"}, "merge_user": {"username": "alice"},
         "created_at": "2026-11-01T00:00:00Z", "merged_at": f"2026-11-0{i}T00:00:00Z"}
        for i in range(1, 4)]))
    (data / "log").write_text("")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DIR", str(data))
    return data


def _calls(data: Path) -> list[list[str]]:
    lines = (data / "log").read_text().splitlines()
    return [line.rstrip("\x1f").split("\x1f") for line in lines if line]


def _names_target(argv: list[str], slug: str) -> bool:
    if argv[0] == "glab":
        return "--hostname" in argv
    if "--hostname" in argv:
        return True
    if "--repo" in argv:
        return argv[argv.index("--repo") + 1] == slug
    return any(a.startswith(f"repos/{slug}") or a.startswith("users/") for a in argv)


def _run(tmp_path: Path, remote: str) -> dict[str, object]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", remote)
    # Run from elsewhere: a call leaning on the cwd remote would find none here.
    return signals.collect(repo, list(signals.PLATFORM_SCANS), platform=True)


def test_every_github_platform_read_names_its_target(stubs: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    scans = _run(tmp_path, "https://github.com/acme/widget.git")
    assert all(scans[name].get("available") for name in signals.PLATFORM_SCANS), scans
    calls = _calls(stubs)
    assert [c for c in calls if not _names_target(c, "acme/widget")] == []
    joined = [" ".join(c) for c in calls]
    for needle in ("--json mergedAt", "--json author,", "api users/", "rules/branches",
                   "/protection"):
        assert any(needle in c for c in joined), needle
    assert all(c[0] == "gh" for c in calls)


def test_every_gitlab_platform_read_names_its_target(stubs: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    scans = _run(tmp_path, "https://gitlab.com/grp/app.git")
    assert all(scans[name].get("available") for name in signals.PLATFORM_SCANS), scans
    calls = _calls(stubs)
    assert [c for c in calls if not _names_target(c, "grp/app")] == []
    joined = [" ".join(c) for c in calls]
    for needle in ("merge_requests?state=merged", "/approvals", "approval_rules",
                   "protected_branches"):
        assert any(needle in c for c in joined), needle
    assert all(c[0] == "glab" for c in calls)
