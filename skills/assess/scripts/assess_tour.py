"""The guided first run of /assess (``/assess --tour``, #1102): a sample repository, its report, one fix.

The sample is ``tally``, a tiny Python expense ledger shipped as a test fixture
(``tests/fixtures/tour_sample/``: the files under ``repo/``, the history in
``history.toml`` and the one fix in ``fix.patch``). It is deliberately imperfect: it has an
instruction file (``AGENTS.md``) and a CI workflow, but the workflow runs ``pytest || true``,
so a failing test never fails the build. It lands on Step 2 (an agent pull request needs a
person to review it) with one blocking gate, ``ci_runs_tests``. The fix deletes the
``|| true``; the re-run lands on Step 3, with ``coverage_gate`` next.

Subcommands, each printing one JSON line:

- ``build DEST [--fixed]`` replays ``history.toml`` into a new git repository at ``DEST``
  (fixed author and dates, so the same SHAs every build); ``--fixed`` also commits the fix.
- ``run DEST`` runs the deterministic core headless on ``DEST``, dated by its HEAD commit
  rather than the clock, and writes ``DEST/.assess/deterministic-report.md``. It prints the
  Step, the next Step's blocking gates and the report path.
- ``fix DEST`` commits the fix: the newcomer's own edit when the tree has one, else
  ``fix.patch`` applied with ``git apply``; ``already_fixed`` when HEAD has it. The commit
  takes the fixed date and message from ``history.toml``.
- ``report --out PATH [--fixed]`` builds and runs in a temporary directory and copies the
  report to ``PATH``: the one command CI needs for the sample report.

``run`` and ``fix`` refuse a path that is not the top of a sample ``build`` made
(``require_sample``: the root commit's author and message), so a wrong or empty path never
commits into, or writes a report into, another repository. The same build gives the same
report, byte for byte (``tests/test_assess_tour.py``).
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "networkx",
#     "grimp",
# ]
# ///
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TypedDict, cast

# Make sibling lib package and orchestrators importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assess_core  # noqa: E402
import assess_report  # noqa: E402

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tour_sample"
REPORT_NAME = "deterministic-report.md"


class TourCommit(TypedDict):
    date: str
    message: str
    paths: list[str]


class TourFix(TypedDict):
    date: str
    message: str


class TourHistory(TypedDict):
    author: str
    commits: list[TourCommit]
    fix: TourFix


class TourResult(TypedDict):
    repo: str
    step: int | None
    next_step: int | None
    next_gates: list[str]
    report: str


def load_history(sample: Path = SAMPLE_DIR) -> TourHistory:
    """The sample's history: its author, its commits in order and the fix commit."""
    with (sample / "history.toml").open("rb") as fh:
        return cast(TourHistory, tomllib.load(fh))


def _git_env(author: str, date: str) -> dict[str, str]:
    """A git environment with no user or system config and a fixed author and date."""
    name, _, email = author.partition(" <")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1"}
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = name
        env[f"GIT_{role}_EMAIL"] = email.rstrip(">")
        env[f"GIT_{role}_DATE"] = date
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return done.stdout


def _copy_lf(src: Path, dest: Path) -> None:
    """Copy a text file with LF endings, whatever the checkout did to it on Windows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    dest.write_bytes(text.encode("utf-8"))


def build(dest: Path, *, fixed: bool = False, sample: Path = SAMPLE_DIR) -> Path:
    """Build the sample repository at ``dest`` (absent or empty) and return its path."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} is not empty; pick a new directory")
    dest.mkdir(parents=True, exist_ok=True)
    history = load_history(sample)
    env = _git_env(history["author"], history["commits"][0]["date"])
    _git(dest, env, "init", "-q", "-b", "main")
    for commit in history["commits"]:
        env = _git_env(history["author"], commit["date"])
        for rel in commit["paths"]:
            _copy_lf(sample / "repo" / rel, dest / rel)
        _git(dest, env, "add", "--", *commit["paths"])
        _git(dest, env, "commit", "-q", "-m", commit["message"])
    if fixed:
        fix(dest, sample=sample)
    return dest


def require_sample(repo: Path, history: TourHistory) -> None:
    """Refuse any ``repo`` that is not the root of a sample ``build`` made.

    The tour commits, so a wrong path (an empty shell variable reads as the current
    directory) must never reach the user's own repository. A sample's root commit has the
    first message and the author of ``history.toml``, and ``repo`` is its top level.
    """
    env = _git_env(history["author"], history["commits"][0]["date"])
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], check=False,
        capture_output=True, encoding="utf-8", errors="replace", env=env,
    )
    top = done.stdout.strip()
    if done.returncode != 0 or Path(top).resolve() != repo.resolve():
        raise ValueError(f"{repo.as_posix()} is not the top of a git repository; pass the path build printed")
    roots = _git(repo, env, "log", "--max-parents=0", "--format=%ae%x00%s", "HEAD").splitlines()
    first = f"{history['author'].partition(' <')[2].rstrip('>')}\0{history['commits'][0]['message']}"
    if roots != [first]:
        raise ValueError(f"{repo.as_posix()} is not a tour sample; pass the path build printed")


def patched_paths(patch: str) -> list[str]:
    """The files a unified diff changes, from its ``+++ b/<path>`` lines."""
    return [line[len("+++ b/"):].strip() for line in patch.splitlines() if line.startswith("+++ b/")]


def _apply(repo: Path, env: dict[str, str], patch: str, *flags: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(repo), "apply", *flags, "-"], input=patch, check=False,
        capture_output=True, encoding="utf-8", errors="replace", env=env,
    )
    return done.returncode == 0


def fix(repo: Path, *, sample: Path = SAMPLE_DIR) -> str:
    """Commit the tour's one fix in the sample at ``repo`` and say how it got there.

    ``applied``: ``fix.patch`` applied to a clean tree and was committed. ``edited``: the
    tree already held an edit (the newcomer made the fix by hand), committed as it is.
    ``already_fixed``: HEAD already has the fix, so nothing was committed. The commit takes
    the fixed date and message from ``history.toml`` either way, and holds only the files
    ``fix.patch`` names. ``repo`` must be a tour sample (``require_sample``).
    """
    history = load_history(sample)
    env = _git_env(history["author"], history["fix"]["date"])
    require_sample(repo, history)
    patch = (sample / "fix.patch").read_text(encoding="utf-8").replace("\r\n", "\n")
    paths = patched_paths(patch)
    if _git(repo, env, "status", "--porcelain", "--untracked-files=no", "--", *paths).strip():
        how = "edited"
    elif _apply(repo, env, patch, "--check"):
        _apply(repo, env, patch)
        how = "applied"
    elif _apply(repo, env, patch, "--reverse", "--check"):
        return "already_fixed"
    else:
        raise ValueError(f"{repo}: fix.patch does not apply and the tree has no edit to commit")
    _git(repo, env, "commit", "-q", "-m", history["fix"]["message"], "--", *paths)
    return how


def run(repo: Path) -> TourResult:
    """Run the deterministic core headless on ``repo`` and write the report beside it."""
    repo = repo.resolve()
    require_sample(repo, load_history())
    env = _git_env("Tour <tour@example.com>", "2026-01-01T00:00:00+00:00")
    run_date = _git(repo, env, "log", "-1", "--format=%cs").strip()
    ctx = assess_core.build_and_publish_run(repo_root=repo, run_date=run_date, non_interactive=True)
    report = assess_report.render_report(assess_report.load_context(repo), repo.name, repo / ".assess")
    out = repo / ".assess" / REPORT_NAME
    out.write_bytes(report.encode("utf-8"))
    level = ctx.get("level") or {}
    nxt = level.get("next_step") or {}
    return {
        "repo": repo.as_posix(),
        "step": level.get("step"),
        "next_step": nxt.get("step"),
        "next_gates": [b.get("gate", "") for b in nxt.get("blockers") or []],
        "report": out.as_posix(),
    }


def report(out: Path, *, fixed: bool = False) -> TourResult:
    """Build the sample in a temporary directory, run it and copy the report to ``out``."""
    with tempfile.TemporaryDirectory(prefix="assess-tour-") as tmp:
        result = run(build(Path(tmp) / "tally", fixed=fixed))
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result["report"], out)
    result["report"] = out.resolve().as_posix()
    result["repo"] = ""
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The /assess guided tour on a sample repository.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="Build the sample repository.")
    p_build.add_argument("dest", type=Path)
    p_build.add_argument("--fixed", action="store_true", help="Also commit the tour's fix.")
    sub.add_parser("run", help="Assess the sample and write its report.").add_argument("dest", type=Path)
    sub.add_parser("fix", help="Apply and commit the tour's fix.").add_argument("dest", type=Path)
    p_report = sub.add_parser("report", help="Build, assess and copy the report to --out.")
    p_report.add_argument("--out", type=Path, required=True)
    p_report.add_argument("--fixed", action="store_true", help="Report on the sample with the fix.")
    args = parser.parse_args(argv)
    if not (SAMPLE_DIR / "history.toml").is_file():
        print(f"error: the tour's sample is not installed here ({SAMPLE_DIR.as_posix()}); "
              "the tour needs the full plugin, not the standalone skill", file=sys.stderr)
        return 2
    try:
        if args.command == "build":
            path = build(args.dest, fixed=args.fixed)
            print(json.dumps({"repo": path.resolve().as_posix()}))
        elif args.command == "run":
            print(json.dumps(run(args.dest)))
        elif args.command == "fix":
            how = fix(args.dest)
            print(json.dumps({"repo": args.dest.resolve().as_posix(), "fix": how}))
        else:
            print(json.dumps(report(args.out, fixed=args.fixed)))
    except (ValueError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or ""
        print(f"error: {e} {detail}".strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
