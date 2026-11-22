"""List bot and agent accounts awaiting confirmation, and record the answers (issue #765).

The first-run discovery behind /assess and /fleet. It reads the git history of
one or more repositories (the window ``ai_usage`` reads) and prints every
account that looks automated but is not yet classified in ``[identities]``,
with its evidence (``lib/account_discovery.py``). It never prompts: the skill
asks the human, then records each answer with ``--confirm``.

The listing holds identities, so it goes to the terminal only. It is never
written into a run artifact, counts-only output or the fleet history.

Run:
    uv run identity_candidates.py <repo> [<repo> ...] [--window 6mo] [--fleet-config FILE] [--json]
    uv run identity_candidates.py <repo> --confirm agent=cursor@example.com --confirm bot=codecov[bot]
    uv run identity_candidates.py --from-list repos.txt --fleet-config FILE [--json]
    uv run identity_candidates.py --from-list repos.txt --confirm bot=ci-runner --config FILE

Several repositories aggregate by account (commits summed, evidence merged,
``repositories`` counted), the /fleet form. ``--confirm CLASS=ACCOUNT`` (CLASS:
``agent``, ``bot`` or ``person``; ACCOUNT: the listed account) writes the answer
into the ``[identities]`` section of ``--config`` (default: the single repo's
``.assess/config.toml``), moving it out of any other class. The section is
regenerated at the end of the file, so comments inside it are not kept; the rest
of the file is kept as written.

Exit codes:
    0  listed, or answers written
    2  usage error: no repository, a path that is not a directory, an unreadable --from-list, an invalid --fleet-config, a bad
       --confirm, a bad --window, a --config that is not valid TOML, --confirm over
       several repos without --config, or git unavailable
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.ai_usage import ALL_TIME, matcher_from_config, resolve_window, window_commits  # noqa: E402
from lib.fleet_config import FleetConfig, FleetConfigError, load_fleet_config, repo_fleet_config  # noqa: E402
from lib.account_discovery import EVIDENCE, Candidate, discover  # noqa: E402
from lib.signals import DEFAULT_WINDOW  # noqa: E402

CLASSES = {"agent": "agents", "bot": "bots", "person": "people"}
_HEADER = re.compile(r"^\s*\[\s*identities\s*\]\s*(#.*)?$")
_ANY_HEADER = re.compile(r"^\s*\[")


class Listed(TypedDict):
    account: str
    name: str
    email: str
    commits: int
    repositories: int
    evidence: list[str]


def collect(repos: list[Path], window: str | None, fleet: FleetConfig | None) -> list[Listed] | str:
    """The aggregated candidates, or the reason git could not answer."""
    merged: dict[str, Listed] = {}
    for repo in repos:
        read = window_commits(repo, None, window)
        if isinstance(read, str):
            return f"{repo}: {read}"
        found: list[Candidate] = discover(read.commits, matcher_from_config(repo_fleet_config(repo, fleet)))
        for c in found:
            key = c["account"].lower()
            row = merged.setdefault(key, {"account": c["account"], "name": c["name"], "email": c["email"],
                                          "commits": 0, "repositories": 0, "evidence": []})
            row["commits"] += c["commits"]
            row["repositories"] += 1
            row["evidence"] = [e for e in EVIDENCE if e in set(row["evidence"]) | set(c["evidence"])]
    return sorted(merged.values(), key=lambda r: (-r["commits"], r["account"].lower()))


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def write_answers(config: Path, answers: list[tuple[str, str]]) -> None:
    """Merge ``answers`` (class key, account) into ``config``'s ``[identities]``.

    The section is regenerated at the end of the file: its entries are kept, its
    comments are not. Every other line is kept as written."""
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    section = tomllib.loads(text).get("identities", {}) if text else {}
    if not isinstance(section, dict):
        raise ValueError("identities: expected a table ([identities])")
    lists = {key: [v for v in section.get(key, []) if isinstance(v, str)] for key in CLASSES.values()}
    for key, account in answers:
        for other in lists.values():
            other[:] = [v for v in other if v.lower() != account.lower()]
        lists[key].append(account)
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if _HEADER.match(line):
            inside = True
            continue
        if inside and _ANY_HEADER.match(line):
            inside = False
        if not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = ["[identities]  # confirmed accounts (#765): literal e-mails or logins"]
    block += [f"{key} = {_toml_list(values)}" for key, values in lists.items() if values]
    body = "\n".join(kept + ([""] if kept else []) + block) + "\n"
    tomllib.loads(body)  # never leave a file the loaders cannot read
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body, encoding="utf-8")


def _answers(raw: list[str]) -> list[tuple[str, str]] | str:
    out = []
    for item in raw:
        cls, sep, account = item.partition("=")
        if not sep or cls.strip() not in CLASSES or not account.strip():
            return f"--confirm {item!r}: expected CLASS=ACCOUNT with CLASS one of agent, bot, person"
        out.append((CLASSES[cls.strip()], account.strip()))
    return out


def _print(rows: list[Listed]) -> None:
    if not rows:
        print("No accounts awaiting confirmation.")
        return
    print(f"{len(rows)} accounts awaiting confirmation (agent, bot or person):")
    for r in rows:
        where = f", {r['repositories']} repositories" if r["repositories"] > 1 else ""
        print(f"- {r['account']}  ({r['name']} <{r['email']}>; {r['commits']} commits{where})"
              f"  evidence: {', '.join(r['evidence'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List bot and agent accounts awaiting confirmation "
                                                 "and record the human's answers in [identities].")
    parser.add_argument("repos", nargs="*", help="Repositories to read (read only unless --confirm).")
    parser.add_argument("--from-list", metavar="FILE",
                        help="A file of repository paths, one per line (# comments allowed), as fleet.py reads.")
    parser.add_argument("--window", default=DEFAULT_WINDOW,
                        help="History window, as ai_usage reads it (default: %(default)s, the headless "
                             "signals default); 'all-time' for full history.")
    parser.add_argument("--fleet-config", metavar="FILE", help="Fleet settings whose [identities] extend each repo's.")
    parser.add_argument("--json", action="store_true", help="Print the candidates as a JSON array.")
    parser.add_argument("--confirm", action="append", default=[], metavar="CLASS=ACCOUNT",
                        help="Record an answer (agent, bot or person); repeatable.")
    parser.add_argument("--config", metavar="FILE",
                        help="Where --confirm writes (default: <repo>/.assess/config.toml for one repo).")
    args = parser.parse_args(argv)
    paths = list(args.repos)
    if args.from_list:
        try:
            lines = Path(args.from_list).read_text(encoding="utf-8").splitlines()
        except OSError as e:
            print(f"error: --from-list {args.from_list}: {e.strerror or e}", file=sys.stderr)
            return 2
        paths += [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not paths:
        print("error: name at least one repository, or --from-list FILE", file=sys.stderr)
        return 2
    repos = [Path(r).expanduser().resolve() for r in paths]
    for repo in repos:
        if not repo.is_dir():
            print(f"error: {repo} is not a directory", file=sys.stderr)
            return 2
    if args.confirm:
        answers = _answers(args.confirm)
        if isinstance(answers, str):
            print(f"error: {answers}", file=sys.stderr)
            return 2
        if args.config is None and len(repos) > 1:
            print("error: --confirm over several repositories needs --config (the --fleet-config file)",
                  file=sys.stderr)
            return 2
        target = Path(args.config) if args.config else repos[0] / ".assess" / "config.toml"
        try:
            write_answers(target, answers)
        except (OSError, UnicodeDecodeError, ValueError) as e:  # TOMLDecodeError is a ValueError
            print(f"error: --config {target}: {e}", file=sys.stderr)
            return 2
        print(f"Recorded {len(answers)} answers in {target}.")
        return 0
    fleet: FleetConfig | None = None
    if args.fleet_config:
        try:
            fleet = load_fleet_config(Path(args.fleet_config))
        except FleetConfigError as e:
            print(f"error: --fleet-config {args.fleet_config}: {e}", file=sys.stderr)
            return 2
    window = None if args.window in ("all", ALL_TIME) else args.window
    try:
        resolve_window(window)
    except ValueError as e:
        print(f"error: --window: {e}", file=sys.stderr)
        return 2
    rows = collect(repos, window, fleet)
    if isinstance(rows, str):
        print(f"error: {rows}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _print(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
