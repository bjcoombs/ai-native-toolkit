"""The guided first run of /assess (``/assess --tour``, #1102): a sample repository, its report, one fix.

The sample is ``tally``, a tiny Python expense ledger. It ships as one data file,
``tour/sample.toml`` (#1149): each file's content, the history and the fix commit's date and
message, with the one fix in ``tour/fix.patch`` beside it. One TOML file keeps the sample
out of this repository's own scans (no scan reads a TOML string as code, docs, instructions
or CI) and lets the standalone ZIP ship it whole. It is deliberately imperfect: it has an
instruction file (``AGENTS.md``) and a CI workflow, but the workflow runs ``pytest || true``,
so a failing test never fails the build. It lands on Step 2 (an agent pull request needs a
person to review it) with one blocking gate, ``ci_runs_tests``. The fix deletes the
``|| true``; the re-run lands on Step 3, with ``coverage_gate`` next.

Subcommands, each printing one JSON line:

- ``build DEST [--fixed]`` writes the files and replays the commits of ``sample.toml`` into a new git repository at ``DEST``
  (fixed author and dates, so the same SHAs every build); ``--fixed`` also commits the fix.
- ``run DEST`` runs the deterministic core headless on ``DEST``, dated by its HEAD commit
  rather than the clock, and writes ``DEST/.assess/deterministic-report.md``. It prints the
  Step, the next Step's blocking gates and the report path.
- ``fix DEST`` commits the fix: the newcomer's own edit when the tree has one, else
  ``fix.patch`` applied with ``git apply``; ``already_fixed`` when HEAD has it. The commit
  takes the fixed date and message from ``sample.toml``.
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
from typing import NotRequired, TypedDict, cast

# Make sibling lib package and orchestrators importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assess_core
import assess_report
from lib.fleet.runner import repository_name

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "tour"
SAMPLE_FILE = "sample.toml"
REPORT_NAME = "deterministic-report.md"


class TourCommit(TypedDict):
    date: str
    message: str
    paths: list[str]


class TourFix(TypedDict):
    date: str
    message: str


class TourFile(TypedDict):
    path: str
    content: str


class TourHistory(TypedDict):
    author: str
    commits: list[TourCommit]
    fix: TourFix
    file: list[TourFile]


class TourResult(TypedDict):
    repo: NotRequired[str]
    step: int | None
    next_step: int | None
    next_gates: list[str]
    report: str


def load_history(sample: Path = SAMPLE_DIR) -> TourHistory:
    """The sample's history: its author, its commits in order and the fix commit."""
    with (sample / SAMPLE_FILE).open("rb") as fh:
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


def _write_lf(text: str, dest: Path) -> None:
    """Write a sample file as UTF-8 with LF endings, whatever the checkout did on Windows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def sample_files(history: TourHistory) -> dict[str, str]:
    """Each sample file's content, by its path in the built repository."""
    return {entry["path"]: entry["content"] for entry in history["file"]}


def build(dest: Path, *, fixed: bool = False, sample: Path = SAMPLE_DIR) -> Path:
    """Build the sample repository at ``dest`` (absent or empty) and return its path."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} is not empty; pick a new directory")
    dest.mkdir(parents=True, exist_ok=True)
    history = load_history(sample)
    files = sample_files(history)
    env = _git_env(history["author"], history["commits"][0]["date"])
    _git(dest, env, "init", "-q", "-b", "main")
    for commit in history["commits"]:
        env = _git_env(history["author"], commit["date"])
        for rel in commit["paths"]:
            _write_lf(files[rel], dest / rel)
        _git(dest, env, "add", "--", *commit["paths"])
        _git(dest, env, "commit", "-q", "-m", commit["message"])
    if fixed:
        fix(dest, sample=sample)
    return dest


def require_sample(repo: Path, history: TourHistory) -> None:
    """Refuse any ``repo`` that is not the root of a sample ``build`` made.

    The tour commits, so a wrong path (an empty shell variable reads as the current
    directory) must never reach the user's own repository. A sample's root commit has the
    first message and the author of ``sample.toml``, and ``repo`` is its top level.
    """
    env = _git_env(history["author"], history["commits"][0]["date"])
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], check=False,
        capture_output=True, encoding="utf-8", errors="replace", env=env,
    )
    top = done.stdout.strip()
    if done.returncode != 0 or Path(top).resolve() != repo.resolve():
        raise ValueError(f"{repo.as_posix()} is not the top of a git repository; pass the path build printed")
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--max-parents=0", "--format=%ae%x00%s", "HEAD"],
        check=False, capture_output=True, encoding="utf-8", errors="replace", env=env,
    )
    roots = log.stdout.splitlines() if log.returncode == 0 else []
    first = f"{history['author'].partition(' <')[2].rstrip('>')}\0{history['commits'][0]['message']}"
    if roots != [first]:
        raise ValueError(f"{repo.as_posix()} is not a tour sample; pass the path build printed")


def patched_paths(patch: str) -> list[str]:
    """The files a unified diff changes, from its ``+++ b/<path>`` lines."""
    return [line[len("+++ b/"):].strip() for line in patch.splitlines() if line.startswith("+++ b/")]


def _code_lines(text: str) -> list[str]:
    """A YAML file's lines without comments, trailing space or blank lines."""
    lines = []
    for line in text.replace("\r\n", "\n").splitlines():
        code = "" if line.lstrip().startswith("#") else line.split(" #", 1)[0]
        if code.strip():
            lines.append(code.rstrip())
    return lines


def _fixed_lines(sample: Path, env: dict[str, str], patch: str, paths: list[str]) -> dict[str, list[str]] | None:
    """Each patched file in its fixed state, as ``_code_lines``, or None if the patch fails.

    The fixed state is the sample's own copy (its ``sample.toml`` content) with ``fix.patch``
    applied in a scratch directory, so it does not depend on what the newcomer's HEAD holds.
    """
    files = sample_files(load_history(sample))
    with tempfile.TemporaryDirectory(prefix="assess-tour-fix-") as tmp:
        for rel in paths:
            _write_lf(files[rel], Path(tmp) / rel)
        if not _git_apply(["git", "apply", "-"], patch, env, cwd=tmp):
            return None
        return {rel: _code_lines((Path(tmp) / rel).read_text(encoding="utf-8")) for rel in paths}


def _is_fixed(texts: dict[str, str], fixed: dict[str, list[str]] | None) -> bool:
    """Whether each file's text is the fixed file once comments and blank lines are set aside.

    So a newcomer who deletes ``|| true`` and keeps the comment above it has made the fix.
    """
    return fixed is not None and all(_code_lines(texts[rel]) == lines for rel, lines in fixed.items())


def _git_apply(argv: list[str], patch: str, env: dict[str, str], cwd: str | None = None) -> bool:
    """Run ``git apply`` on ``patch``, fed as LF bytes.

    Bytes, not text: a text-mode pipe on Windows turns every "\n" into "\r\n", and the
    patch then matches no line of the LF sample.
    """
    done = subprocess.run(
        argv, cwd=cwd, input=patch.replace("\r\n", "\n").encode("utf-8"), check=False,
        capture_output=True, env=env,
    )
    return done.returncode == 0


def _apply(repo: Path, env: dict[str, str], patch: str, *flags: str) -> bool:
    return _git_apply(["git", "-C", str(repo), "apply", *flags, "-"], patch, env)


def _read(path: Path) -> str:
    """A file's text, or "" when it is missing (a newcomer may delete the workflow)."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def fix(repo: Path, *, sample: Path = SAMPLE_DIR) -> str:
    """Commit the tour's one fix in the sample at ``repo`` and say how it got there.

    ``applied``: ``fix.patch`` applied to a clean tree and was committed. ``edited``: the
    newcomer made the fix by hand, and their edit, which matches the fixed file once
    comments and blank lines are set aside, was committed as it is. Any other edit to a file
    the patch names raises ``ValueError`` with its diff; edits to other files stay out.
    ``already_fixed``: HEAD already has the fix, by the same comparison, so nothing was
    committed. The commit takes
    the fixed date and message from ``sample.toml`` either way, and holds only the files
    ``fix.patch`` names. ``repo`` must be a tour sample (``require_sample``).
    """
    history = load_history(sample)
    env = _git_env(history["author"], history["fix"]["date"])
    require_sample(repo, history)
    patch = (sample / "fix.patch").read_text(encoding="utf-8").replace("\r\n", "\n")
    paths = patched_paths(patch)
    fixed = _fixed_lines(sample, env, patch, paths)
    if _git(repo, env, "status", "--porcelain", "--untracked-files=no", "--", *paths).strip():
        tree = {rel: _read(repo / rel) for rel in paths}
        if not _is_fixed(tree, fixed):
            diff = _git(repo, env, "diff", "--", *paths)
            raise ValueError(f"the edit to {', '.join(paths)} is not the tour's fix "
                             f"(delete `|| true` from the pytest line):\n{diff}")
        how = "edited"
    elif _apply(repo, env, patch, "--check"):
        _apply(repo, env, patch)
        how = "applied"
    elif _is_fixed({rel: _git(repo, env, "show", f"HEAD:{rel}") for rel in paths}, fixed):
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
    report = assess_report.render_report(assess_report.load_context(repo), repository_name(repo),
                                        repo / ".assess")
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
    del result["repo"]  # the temporary sample is gone
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
    if not (SAMPLE_DIR / SAMPLE_FILE).is_file():
        print(f"error: the tour's sample is missing ({(SAMPLE_DIR / SAMPLE_FILE).as_posix()}); "
              "the skill's install is incomplete", file=sys.stderr)
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
