"""The calibration corpus: expected condition states per repository, a diff and the discrimination rule (#842).

The corpus is a set of public repositories, cloned by hand at pinned commits in a ghsync
layout (``<root>/<path>/<repo>/<repo>-main``, the layout ``lib/fleet/runner.py``
``discover_root`` reads). The clones stay out of this repository; one expected-state file per
clone is in it, under ``skills/assess/calibration/corpus/`` (``CORPUS_DIR``).
``scripts/calibration_corpus.py`` is the command.

**Expected-state file** (``<key>.json``)::

    {"corpus_version": 1, "repo": "h:<16 hex>", "pinned": "h:<16 hex>",
     "levels_version": str, "states": {condition id: state, ...}}

``repo`` is the clone's layout id (``runner.Target.repo``) and ``pinned`` its ``HEAD`` commit,
each hashed with ``lib.counts_only.content_free.hash_value`` under ``CORPUS_SALT``, so the file
holds no repository name and no commit sha a search could find (a sha finds its repository,
#907); the key is ``repo`` without its ``h:``. The salt is public, so the hash hides a name
from a search, not from a guess: anyone can hash a candidate ``owner/name`` under it and
confirm a match, then hash that repository's commits to find ``pinned``. ``states`` holds every condition of the model
and its ``state`` (``lib.scoring.level_tally.STATES``) from ``level_result``, the function
``/assess`` and ``fleet --levels`` run.

**The run** (``observe``): every clone under the root is scanned as ``/fleet`` scans it
(``runner.run_fleet``, counts-only, offline, window ``WINDOW``, the committed tree of
``HEAD``), and each ok row becomes its condition states (``fleet_level.repo_level`` over the
default model). Measuring the commit's tree, offline and with the history window counted back
from the commit (``lib.history.history_window.anchored_since``), keeps the states a function
of the pinned commit and the toolkit code alone.

- ``platform``: the backlog-ready row's level 2 (#1324) reads the issue tracker, which the
  offline scan never reaches, so offline it reads ``not_measured`` in every repository. With a
  ``platform`` callable (``calibration_corpus.py --platform``) ``observe`` runs the fleet platform
  pass for ``PLATFORM_READS`` alone (``backlog_ready``) after the offline scan; every other block
  stays a function of the pinned commit. Those level 2 states move as the trackers move, so a
  corpus seeded with ``--platform`` is diffed with it, and a ``backlog-ready.2`` diff line can be
  a tracker's own change rather than the toolkit's.
- ``seed``: an expected-state file per scanned clone, from this toolkit's run.
- ``refusals``: before any scan, an expected file with no clone under the root
  (``no_clone``), or a clone whose ``HEAD`` is not its pinned commit (``wrong_commit``) is
  refused; a clone that fails to scan is refused after (``scan_failed``). Any refusal
  stops the diff, so a clean diff always covers the whole corpus.
- ``diff_states``: one line per condition whose state differs,
  ``<key> <condition>: expected <state>, got <state>``, ``absent`` for a condition one side
  lacks (the model added or removed it).
- ``discrimination``: the #842 discrimination rule, the way a condition enters the model
  (``lib/fleet/condition_demotion.py`` is the way out). A condition met in no repository of
  the corpus (``met_in_none``) or in every one (``met_in_all``) does not separate
  repositories, so it is reported non-discriminating. Only ``met`` counts as meeting it:
  ``unmet``, ``not_applicable``, ``declined`` and ``not_measured`` do not. A condition no
  repository measured (every state ``not_measured``) is reported apart, as
  ``not_measured_anywhere``, since the offline run cannot read it at all rather than every
  repository lacking it. It reads the run's states, or the
  expected files alone (``report``), so it needs no clones.

- ``row_split``: how a row's conditions fall across the corpus, ``{condition: {state: count}}``
  over every state, read from the expected files alone; ``split_lines`` prints it, and
  ``tests/test_backlog_ready.py::test_corpus_discriminates`` asserts from it that
  ``backlog-ready.2`` is met in at least one repository and unmet in at least one.

Tests: ``tests/test_calibration_corpus.py``.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from lib.counts_only.content_free import hash_value
from lib.fleet.fleet_level import repo_level
from lib.fleet.runner import STATUS_OK, FleetRow, ScanFn, ScanOptions, Target, commit_state, run_fleet, scan_target
from lib.scoring.level_tally import STATES
from lib.scoring.levels import MaturityModel

CORPUS_VERSION = 1
CORPUS_DIR = Path(__file__).resolve().parents[3] / "calibration" / "corpus"
CORPUS_SALT = "assess-calibration-corpus"
WINDOW = "12mo"  # the calibration rounds' --window
ABSENT = "absent"
MET_IN_NONE, MET_IN_ALL = "met_in_none", "met_in_all"
NOT_MEASURED_ANYWHERE = "not_measured_anywhere"
NO_CLONE, WRONG_COMMIT, SCAN_FAILED = "no_clone", "wrong_commit", "scan_failed"
_FIELDS = frozenset({"corpus_version", "repo", "pinned", "levels_version", "states"})
PLATFORM_READS = ("backlog_ready",)  # the platform blocks a --platform corpus run reads (#1324)


class CorpusError(ValueError):
    """An expected-state file that cannot be read as one."""


class ExpectedState(TypedDict):
    corpus_version: int
    repo: str  # hash_value(layout id, CORPUS_SALT)
    pinned: str  # hash_value(HEAD sha, CORPUS_SALT)
    levels_version: str
    states: dict[str, str]  # condition id -> one of level_tally.STATES


class Observed(TypedDict):
    """One clone's run: its hashed id, hashed ``HEAD`` and condition states."""

    repo: str
    pinned: str
    states: dict[str, str]


class Refusal(TypedDict):
    repo: str  # the hashed id
    reason: str  # NO_CLONE, WRONG_COMMIT or SCAN_FAILED


class Discrimination(TypedDict):
    condition: str
    met: int  # repositories meeting it
    repos: int  # repositories in the corpus
    verdict: str  # MET_IN_NONE, MET_IN_ALL or NOT_MEASURED_ANYWHERE


def repo_key(repo_id: str) -> str:
    return hash_value(repo_id, CORPUS_SALT)


def file_name(repo: str) -> str:
    return repo.removeprefix("h:") + ".json"


def _check(data: Any, where: str) -> ExpectedState:
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise CorpusError(f"{where}: fields must be {sorted(_FIELDS)}")
    if data["corpus_version"] != CORPUS_VERSION:
        raise CorpusError(f"{where}: corpus_version {data['corpus_version']!r}, expected {CORPUS_VERSION}")
    if not all(isinstance(data[k], str) for k in ("repo", "pinned", "levels_version")):
        raise CorpusError(f"{where}: repo, pinned and levels_version must be strings")
    states = data["states"]
    if not isinstance(states, dict) or not all(isinstance(k, str) and v in STATES for k, v in states.items()):
        raise CorpusError(f"{where}: states must map condition ids to one of {list(STATES)}")
    if file_name(data["repo"]) != Path(where).name:
        raise CorpusError(f"{where}: file name must be {file_name(data['repo'])}")
    return ExpectedState(corpus_version=data["corpus_version"], repo=data["repo"], pinned=data["pinned"],
                         levels_version=data["levels_version"], states=dict(states))


def load_corpus(corpus_dir: Path) -> list[ExpectedState]:
    """Every ``*.json`` expected-state file in ``corpus_dir``, sorted by ``repo``; none for no directory."""
    out: list[ExpectedState] = []
    for path in sorted(corpus_dir.glob("*.json")) if corpus_dir.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CorpusError(f"{path.name}: {e}") from e
        out.append(_check(data, path.name))
    return sorted(out, key=lambda e: e["repo"])


def dumps_expected(expected: ExpectedState) -> str:
    return json.dumps(expected, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_expected(corpus_dir: Path, expected: ExpectedState) -> Path:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    path = corpus_dir / file_name(expected["repo"])
    path.write_text(dumps_expected(expected), encoding="utf-8", newline="\n")
    return path


def states_of(model: MaturityModel, row: FleetRow) -> dict[str, str]:
    """Each condition's state for one scanned row, as ``fleet --levels`` reads it."""
    return {c["id"]: c["state"] for c in repo_level(model, row)["conditions"]}


def pinned_key(checkout: Path) -> str | None:
    head = commit_state(checkout)["head_sha"]
    return None if head is None else hash_value(head, CORPUS_SALT)


def commit_refusals(expected: Sequence[ExpectedState], targets: Sequence[Target],
                    pinned: Callable[[Path], str | None] = pinned_key) -> list[Refusal]:
    """An expected file with no clone, or a clone whose ``HEAD`` is not its pinned commit."""
    by_key = {repo_key(t.repo): t for t in targets}
    out: list[Refusal] = []
    for e in expected:
        target = by_key.get(e["repo"])
        if target is None:
            out.append(Refusal(repo=e["repo"], reason=NO_CLONE))
        elif pinned(target.checkout) != e["pinned"]:
            out.append(Refusal(repo=e["repo"], reason=WRONG_COMMIT))
    return out


# The platform pass over the offline rows: ``lib.fleet.fleet_platform.run_platform`` bound to
# ``PLATFORM_READS`` by the command, or a stub in tests.
PlatformFn = Callable[[list[FleetRow], Sequence[Target], ScanOptions], list[FleetRow]]


def observe(targets: Sequence[Target], model: MaturityModel, jobs: int,
            scan: ScanFn = scan_target,
            platform: PlatformFn | None = None) -> tuple[list[Observed], list[Refusal]]:
    """Scan ``targets`` as ``/fleet`` does; each ok row's states, and a refusal per failed scan.
    With ``platform`` its blocks are joined into the ok rows before their states are read."""
    options = ScanOptions(window=WINDOW)
    rows = run_fleet(targets, options, jobs, scan)
    if platform is not None:
        joined = {r["repo"]: r for r in platform([r for r in rows if r["status"] == STATUS_OK], targets, options)}
        rows = [joined.get(r["repo"], r) for r in rows]
    observed: list[Observed] = []
    refused: list[Refusal] = []
    for row in rows:
        key = repo_key(row["repo"])
        sha = row["head_sha"]
        if row["status"] != STATUS_OK or sha is None:
            refused.append(Refusal(repo=key, reason=SCAN_FAILED))
            continue
        observed.append(Observed(repo=key, pinned=hash_value(sha, CORPUS_SALT), states=states_of(model, row)))
    return observed, refused


def seed(observed: Iterable[Observed], model: MaturityModel) -> list[ExpectedState]:
    """An expected-state file per observed clone, pinned at the commit it measured."""
    return [ExpectedState(corpus_version=CORPUS_VERSION, repo=o["repo"], pinned=o["pinned"],
                          levels_version=model["version"], states=dict(o["states"]))
            for o in sorted(observed, key=lambda o: o["repo"])]


def diff_states(repo: str, expected: Mapping[str, str], got: Mapping[str, str]) -> list[str]:
    """One line per condition whose state moved, sorted by condition id."""
    key = repo.removeprefix("h:")
    return [f"{key} {cid}: expected {expected.get(cid, ABSENT)}, got {got.get(cid, ABSENT)}"
            for cid in sorted(set(expected) | set(got)) if expected.get(cid) != got.get(cid)]


def diff_corpus(expected: Sequence[ExpectedState], observed: Sequence[Observed]) -> list[str]:
    """Every diff line of the corpus, by repository then condition."""
    got = {o["repo"]: o["states"] for o in observed}
    return [line for e in sorted(expected, key=lambda e: e["repo"])
            for line in diff_states(e["repo"], e["states"], got.get(e["repo"], {}))]


def discrimination(states_by_repo: Mapping[str, Mapping[str, str]]) -> list[Discrimination]:
    """The conditions no repository meets, or every repository meets, sorted by id."""
    repos = len(states_by_repo)
    out: list[Discrimination] = []
    if repos == 0:
        return out
    for cid in sorted({c for states in states_by_repo.values() for c in states}):
        met = sum(states.get(cid) == "met" for states in states_by_repo.values())
        if all(states.get(cid) == "not_measured" for states in states_by_repo.values()):
            verdict = NOT_MEASURED_ANYWHERE
        elif met in (0, repos):
            verdict = MET_IN_NONE if met == 0 else MET_IN_ALL
        else:
            continue
        out.append(Discrimination(condition=cid, met=met, repos=repos, verdict=verdict))
    return out


def discrimination_lines(rows: Sequence[Discrimination]) -> list[str]:
    return [f"non-discriminating {r['condition']}: {r['verdict']} ({r['met']} of {r['repos']})" for r in rows]


def refusal_lines(refusals: Sequence[Refusal]) -> list[str]:
    return [f"refused {r['repo'].removeprefix('h:')}: {r['reason']}" for r in sorted(refusals, key=lambda r: r["repo"])]


def row_split(states_by_repo: Mapping[str, Mapping[str, str]], row: str) -> dict[str, dict[str, int]]:
    """Each ``<row>.<level>`` condition's count of repositories per state (every state, 0 kept)."""
    prefix = f"{row}."
    cids = sorted({c for states in states_by_repo.values() for c in states if c.startswith(prefix)})
    return {cid: {s: sum(states.get(cid) == s for states in states_by_repo.values()) for s in STATES}
            for cid in cids}


def split_lines(split: Mapping[str, Mapping[str, int]], repos: int) -> list[str]:
    """``<condition>: met 2, unmet 7, not_measured 3 (of 12)``, states with no repository left out."""
    return [f"{cid}: " + ", ".join(f"{s} {n}" for s, n in counts.items() if n) + f" (of {repos})"
            for cid, counts in split.items()]
