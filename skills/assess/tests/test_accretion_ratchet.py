"""Comprehensive contract suite for the accretion-ratchet scanner.

Fixtures build synthetic git histories in tmp dirs; expected values are
hand-computed in each test's docstring so the contract is auditable. The
scanner's job is to flag files whose accumulated line count only ever ratchets
upward - net growth *with almost no deletion pressure* across multiple commits -
while leaving healthy churn, renames, binaries, and single-touch artifacts
alone. These tests pin that behaviour and, crucially, the determinism contract:
the author-time ordering must make the output byte-identical run to run.

Author/committer identity and dates are pinned in every fixture commit so the
``%at`` ordering and the resulting net-delta accumulation are reproducible on
CI. The ambient git config is already neutralised process-wide by the package
``conftest.py`` (GIT_CONFIG_GLOBAL/SYSTEM -> /dev/null), so commits never trip
ambient signing.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import lib.accretion_ratchet as ar
from lib.accretion_ratchet import (
    DELETION_FRACTION_THRESHOLD,
    MIN_COMMITS_FOR_ACCRETION,
    AccretionFile,
    _accumulate_history,
    _build_accretion_file,
    _FileHistory,
    _is_monotonic_nondecreasing,
    _repo_top,
    scan_accretion_ratchet,
)

# A fixed clock so every commit's author/committer time is deterministic. Each
# commit advances the clock by one day, which both pins the ``%at`` sort order
# and gives the time-span readout a known value.
_BASE_EPOCH = 1_700_000_000  # 2023-11-14T22:13:20Z
_DAY = 86_400


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    full_env = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=full_env)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev Human")
    return repo


class _Clock:
    """Monotone per-commit clock: each call returns the next day's epoch.

    Pinning author *and* committer time to the same advancing value makes the
    scanner's ``(author_time, sha)`` sort fully determined by commit order, so
    the accumulation sequence - and the flagged output - is reproducible.
    """

    def __init__(self) -> None:
        self._n = 0

    def env(self) -> dict[str, str]:
        stamp = f"{_BASE_EPOCH + self._n * _DAY} +0000"
        self._n += 1
        return {
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        }


def _commit(repo: Path, rel: str, text: str, clock: _Clock,
            message: str = "change") -> None:
    """Write ``text`` to ``rel``, stage, and commit at the clock's next tick."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, env=clock.env())


def _lines(n: int, *, start: int = 0, width: int = 12) -> str:
    """A file body of ``n`` distinct lines (distinct so numstat counts churn).

    Each line is unique and ``width`` chars wide, so replacing the body deletes
    the old lines and adds the new ones - a real numstat delta, not a no-op.
    """
    return "".join(f"L{i + start:0{width}d}\n" for i in range(n))


def _flagged_paths(repo: Path, **kw) -> list[str]:
    return [f.path for f in scan_accretion_ratchet(repo, **kw).files]


# --- 1. Deletion-fraction discrimination -------------------------------------

def test_deletion_fraction_discriminates_refactor_from_ratchet(tmp_path: Path) -> None:
    """A healthy refactor is spared; a pure ratchet is flagged.

    Two files, three commits each (clearing the multi-commit gate):

    * ``refactor.py`` - grows then is reworked: 2000 lines added, 1500 deleted
      across its history => deletion fraction 1500 / 3500 = 0.4286, far above the
      0.15 default. Maintained by rewriting, so it is NOT flagged - regardless of
      whether its net drifts up.
    * ``ratchet.py`` - appended to only: 510 added, 10 deleted => 10 / 520 =
      0.0192, well below 0.15, and its running net never falls back. Flagged.

    Only the ratchet should surface.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # ratchet.py: append-only growth. Verified numstat per commit below.
    #   c1: write 200 lines               -> +200 / -0   (net 200)
    #   c2: keep 200, append 200          -> +200 / -0   (net 400)
    #   c3: rewrite 10 lines in place,
    #       append 110 more               -> +120 / -10  (net 510)
    # Totals: 520 added, 10 deleted => fraction 10 / 530 = 0.0189 < 0.15.
    # Running net 200 -> 400 -> 510 is monotonic. Flagged.
    _commit(repo, "ratchet.py", _lines(200), clock)
    _commit(repo, "ratchet.py", _lines(200) + _lines(200, start=200), clock)
    body = (_lines(190) + _lines(10, start=9000)
            + _lines(200, start=200) + _lines(110, start=400))
    _commit(repo, "ratchet.py", body, clock)

    # refactor.py: heavy churn. Grow big, then rewrite large swaths so deletions
    # are a large share of total churn.
    _commit(repo, "refactor.py", _lines(1000), clock)
    _commit(repo, "refactor.py", _lines(500, start=10000) + _lines(500, start=2000), clock)
    _commit(repo, "refactor.py", _lines(1000, start=20000), clock)

    scan = scan_accretion_ratchet(repo)
    flagged = {f.path for f in scan.files}

    assert "ratchet.py" in flagged
    assert "refactor.py" not in flagged
    # The ratchet's deletion fraction is well under the default threshold.
    ratchet = next(f for f in scan.files if f.path == "ratchet.py")
    assert ratchet.deletion_fraction < DELETION_FRACTION_THRESHOLD
    assert ratchet.net_additions > 0


# --- 2. Rename handling ------------------------------------------------------

def test_rename_is_not_flagged_as_accretion(tmp_path: Path) -> None:
    """A create -> rename -> add sequence is not a ratchet.

    With ``--no-renames`` (the scanner's mode) a rename reads as a full delete of
    the old path plus a full add of the new path. The old path nets to zero (and
    is filtered: net <= 0). The new path appears in only one commit (the rename
    itself) and would need ``MIN_COMMITS_FOR_ACCRETION`` (3) touches to qualify;
    the single later edit gives it two, still under the gate. So neither path is
    flagged - the rename produces no false positive.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    _commit(repo, "old_name.py", _lines(300), clock, "create")
    # Rename: git mv, content unchanged. Under --no-renames this is del old / add new.
    _git(repo, "mv", "old_name.py", "new_name.py")
    _git(repo, "commit", "-q", "-m", "rename", env=clock.env())
    # One later edit under the new name.
    _commit(repo, "new_name.py", _lines(300) + _lines(50, start=300), clock, "extend")

    flagged = _flagged_paths(repo)
    assert "old_name.py" not in flagged
    assert "new_name.py" not in flagged


# --- 3. Degenerate history ---------------------------------------------------

def test_squashed_history_is_available_but_unreliable(tmp_path: Path) -> None:
    """One commit touching many files => available True, reliable False.

    A squashed import gives every file exactly one commit, so the per-file
    commit-count distribution is flat (p95 == 1) over enough active files
    (>= MIN_ACTIVE_FILES_FOR_DEGENERACY = 5). ``churn_is_degenerate`` returns
    True, so the scan reports ``reliable=False`` while still being ``available``.
    No file clears the 3-commit gate, so nothing is flagged either.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    files = {f"mod/f{i}.py": _lines(100 + i) for i in range(8)}
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "squashed import", env=clock.env())

    scan = scan_accretion_ratchet(repo)
    assert scan.available is True
    assert scan.reliable is False
    assert scan.files == []


# --- 4. Binary file guard ----------------------------------------------------

def test_binary_files_are_skipped(tmp_path: Path) -> None:
    """A binary file (numstat ``-``) carries no line signal and is not flagged.

    Git emits ``-\t-\t<path>`` for binary content; the parser skips rows whose
    add/remove columns are not digits. Even committed repeatedly (clearing the
    multi-commit gate), the binary never accumulates additions, so it can never
    appear in the flagged set. A text file committed alongside it under pure
    accretion still surfaces - proving the binary is skipped, not the whole
    commit.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    def write_binary(rel: str, n: int) -> None:
        # NUL bytes force git to treat the blob as binary (numstat '-').
        (repo / rel).write_bytes(bytes([0, 1, 2, 255] * n))

    for i in range(1, 4):
        write_binary("asset.bin", 100 * i)
        # A text file that only grows, committed in the same commits.
        (repo / "grow.py").write_text(_lines(200 * i), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c{i}", env=clock.env())

    scan = scan_accretion_ratchet(repo)
    flagged = {f.path for f in scan.files}
    assert "asset.bin" not in flagged
    # The accompanying append-only text file is flagged, so the commits were
    # scanned - the binary was filtered specifically.
    assert "grow.py" in flagged


# --- 5. Ordering reproducibility ---------------------------------------------

def test_scan_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Two scans of one repo serialize to byte-identical JSON.

    The scanner sorts history on ``(author_time, sha)`` rather than trusting
    git's emission order, so the net-delta accumulation - and the flagged set,
    its order, and every field - must be reproducible. This pins that contract:
    serialize the full summary twice and assert exact equality.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # A handful of files with varied histories so the flagged set is non-trivial.
    for i in range(1, 5):
        _commit(repo, "a.py", _lines(100 * i), clock)
    for i in range(1, 4):
        _commit(repo, "b.py", _lines(80 * i, start=900), clock)
    for i in range(1, 4):
        # A churny file that should be excluded - keeps the sort non-trivial.
        _commit(repo, "c.py", _lines(50, start=i * 1000), clock)

    first = json.dumps(scan_accretion_ratchet(repo).summary(), sort_keys=True)
    second = json.dumps(scan_accretion_ratchet(repo).summary(), sort_keys=True)
    assert first == second
    # And the flagged set is actually populated, so equality isn't vacuous.
    assert scan_accretion_ratchet(repo).files


# --- 6. Multi-commit gate ----------------------------------------------------

def test_single_commit_growth_is_not_flagged(tmp_path: Path) -> None:
    """A file that grows large in one commit is below the multi-commit gate.

    Accretion is a property of *repeated* growth: a file appearing in fewer than
    ``MIN_COMMITS_FOR_ACCRETION`` (3) commits is filtered, even if that single
    commit adds a thousand lines and deletes nothing (a perfect-looking ratchet
    in miniature). This guards against a one-shot generated file reading as
    accretion. Other files in the repo provide enough history that the scan is
    reliable, isolating the gate as the reason this file is spared.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # Background history so the repo isn't degenerate (>=5 active, multi-commit).
    for i in range(1, 4):
        _commit(repo, "background.py", _lines(40 * i, start=7000), clock)
    for name in ("p.py", "q.py", "r.py", "s.py"):
        for i in range(1, 4):
            _commit(repo, name, _lines(20 * i, start=8000), clock)

    # The file under test: one commit, large append-only body, zero deletions.
    _commit(repo, "oneshot.py", _lines(1000), clock, "generated in one shot")

    flagged = _flagged_paths(repo)
    assert "oneshot.py" not in flagged


def test_two_commit_growth_is_not_flagged(tmp_path: Path) -> None:
    """Exactly MIN_COMMITS_FOR_ACCRETION - 1 touches is still below the gate.

    The boundary: a monotonically growing, zero-deletion file touched in only
    two commits must not be flagged, while the same pattern at three commits is.
    This pins the gate at its exact threshold.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # Two-commit grower: must NOT be flagged.
    _commit(repo, "two.py", _lines(100), clock)
    _commit(repo, "two.py", _lines(200), clock)

    # Three-commit grower with the same shape: MUST be flagged, proving the gate
    # is the only thing keeping two.py out.
    _commit(repo, "three.py", _lines(100, start=300), clock)
    _commit(repo, "three.py", _lines(200, start=300), clock)
    _commit(repo, "three.py", _lines(300, start=300), clock)

    flagged = _flagged_paths(repo)
    assert "two.py" not in flagged
    assert "three.py" in flagged
    assert MIN_COMMITS_FOR_ACCRETION == 3


# --- Monotonicity: a late cut clears the flag --------------------------------

def test_late_refactor_clears_the_ratchet(tmp_path: Path) -> None:
    """A file that grows, then is cut back below an earlier high, is not flagged.

    Monotonicity is judged off the running net-delta sequence, not the
    endpoints. Three growth commits push net up; a fourth deletes enough to drop
    net below an earlier value. That single step down breaks the ratchet even
    though the file still has a positive net - the deletion pressure the signal
    rewards. (Deletions here also stay a small share of churn, so it is the
    monotonicity test, not the deletion-fraction filter, doing the work.)
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # Grow: net climbs 300 -> 600 -> 900.
    _commit(repo, "f.py", _lines(300), clock)
    _commit(repo, "f.py", _lines(600), clock)
    _commit(repo, "f.py", _lines(900), clock)
    # Cut: drop to 250 lines. Net falls to 250, below the first commit's 300.
    # This single backward step makes the sequence non-monotonic.
    _commit(repo, "f.py", _lines(250), clock)

    # Provide background so the scan is reliable and f.py is the only variable.
    for name in ("x.py", "y.py", "z.py", "w.py"):
        for i in range(1, 4):
            _commit(repo, name, _lines(15 * i, start=9000), clock)

    flagged = _flagged_paths(repo)
    assert "f.py" not in flagged


# --- 7. Self-test on the assess repo ----------------------------------------

def test_self_scan_on_assess_repo_is_well_formed(tmp_path: Path) -> None:
    """Scanning this repo's own history produces only well-formed flags.

    A dogfood guard run against real, human-maintained history. The repo *does*
    contain files whose net line count has only ever grown (a test suite that
    keeps gaining cases, a reference doc that keeps gaining sections) - that is
    not a false positive, it is the signal working on real data. What must hold
    is that every flagged record honestly satisfies the definition it claims:
    positive net additions, at least ``MIN_COMMITS_FOR_ACCRETION`` commits, and a
    deletion fraction strictly under the threshold the scan reports. A "false
    positive" here would be a record that is flagged while violating its own
    contract - that is what this rules out. The scanner may degrade to
    unavailable in a packaging context with no git history; that is acceptable.

    The flagged set must also be deterministically sorted (net descending, path
    ascending) - the same ordering contract the byte-identical test pins, checked
    here against real history rather than a fixture.
    """
    repo_root = Path(__file__).resolve().parents[1]  # skills/assess/
    scan = scan_accretion_ratchet(repo_root)

    if not scan.available:
        # No git history reachable (e.g. exported tree) - nothing to assert.
        return

    for f in scan.files:
        # Every flagged record must satisfy the contract it claims.
        assert f.net_additions > 0, f
        assert f.commit_count >= MIN_COMMITS_FOR_ACCRETION, f
        assert f.deletion_fraction < scan.deletion_fraction_threshold, f
        assert 0.0 <= f.deletion_fraction < 1.0, f
        assert f.time_span_months >= 0.0, f

    # Sorted by net additions descending, then path ascending - the public
    # ordering contract, holding on real history.
    keys = [(-f.net_additions, f.path) for f in scan.files]
    assert keys == sorted(keys)


# --- AccretionFile field contract --------------------------------------------

def test_accretion_file_fields_and_to_dict(tmp_path: Path) -> None:
    """A flagged file's fields and ``to_dict`` rounding match the contract.

    Five append-only commits over four days (one per clock tick) to a single
    growing file. net_additions is the final additions - deletions; commit_count
    is the touch count; deletion_fraction rounds to 4 dp in ``to_dict`` and
    time_span_months rounds to 1 dp. The span is 4 days (first to fifth commit) /
    30.44 ~= 0.13 months -> rounds to 0.1.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # Five commits, pure append, no deletions.
    for i in range(1, 6):
        _commit(repo, "grow.py", _lines(100 * i), clock)
    # Background so the scan is reliable.
    for name in ("a.py", "b.py", "c.py", "d.py"):
        for i in range(1, 4):
            _commit(repo, name, _lines(10 * i, start=5000), clock)

    scan = scan_accretion_ratchet(repo)
    grow = next((f for f in scan.files if f.path == "grow.py"), None)
    assert grow is not None
    assert isinstance(grow, AccretionFile)
    assert grow.commit_count == 5
    assert grow.net_additions == 500  # 100..500 added, nothing removed
    assert grow.deletion_fraction == 0.0

    d = grow.to_dict()
    assert d["path"] == "grow.py"
    assert d["net_additions"] == 500
    assert d["commit_count"] == 5
    assert d["deletion_fraction"] == 0.0
    # round(x, 1) on the to_dict span: keys present and numeric.
    assert isinstance(d["time_span_months"], float)


# --- Threshold sensitivity (as-merged semantics) -----------------------------

def test_caller_threshold_is_honored_end_to_end(tmp_path: Path) -> None:
    """The caller's deletion_threshold is the cut actually applied by the scan.

    Builds a file at deletion fraction ~0.20: 400 lines added, 100 deleted
    across the history (100 / 500 = 0.20), monotonic, multi-commit. At the 0.15
    default it is dropped (0.20 >= 0.15). At ``deletion_threshold=0.30`` it is
    admitted (0.20 < 0.30) - and the reported threshold on the scan matches the
    one applied. This is the end-to-end form of the task-1-fix regression: the
    caller's value is honored, not a hard-coded module constant.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()

    # c1: write 200 lines                 -> +200 / -0   (net 200)
    # c2: keep 200, append 200             -> +200 / -0   (net 400)
    # c3: keep first 300, replace last 100 -> +100 / -100 (net 400, still up)
    # Totals: 500 added, 100 deleted => fraction 100 / 600 = 0.1667, just over the
    # 0.15 default. Running net 200 -> 400 -> 400 is monotonic non-decreasing.
    _commit(repo, "g.py", _lines(200), clock)
    _commit(repo, "g.py", _lines(400), clock)
    body = _lines(300) + _lines(100, start=99000)
    _commit(repo, "g.py", body, clock)

    # Background history so the scan is reliable.
    for name in ("h.py", "i.py", "j.py", "k.py"):
        for i in range(1, 4):
            _commit(repo, name, _lines(10 * i, start=6000), clock)

    # Measure g.py's real deletion fraction (threshold 1.0 admits everything)
    # so the assertions can bracket the threshold around it.
    full = scan_accretion_ratchet(repo, deletion_threshold=1.0)
    g_full = next(f for f in full.files if f.path == "g.py")
    frac = g_full.deletion_fraction
    assert frac > DELETION_FRACTION_THRESHOLD  # excluded at the default

    # Below the file's fraction -> dropped; the reported threshold is the cut used.
    below = scan_accretion_ratchet(repo, deletion_threshold=frac - 0.01)
    assert below.deletion_fraction_threshold == frac - 0.01
    assert all(f.path != "g.py" for f in below.files)

    # Above the file's fraction -> admitted.
    above = scan_accretion_ratchet(repo, deletion_threshold=frac + 0.01)
    assert any(f.path == "g.py" for f in above.files)


# --- Availability degrade -----------------------------------------------------

def test_non_git_directory_is_unavailable(tmp_path: Path) -> None:
    """A plain directory (not a git repo) degrades to available=False, never raises."""
    plain = tmp_path / "nogit"
    plain.mkdir()
    (plain / "a.py").write_text(_lines(50), encoding="utf-8")

    scan = scan_accretion_ratchet(plain)
    assert scan.available is False
    assert scan.files == []
    assert scan.reason


def test_empty_repo_has_no_commit_history(tmp_path: Path) -> None:
    """An initialised repo with zero commits reports 'no commit history'.

    ``_accumulate_history`` returns an empty map (git log fails on a repo with no
    HEAD), which the scanner reports as unavailable rather than a clean scan.
    """
    repo = _init_repo(tmp_path)  # init + config, but no commit yet
    scan = scan_accretion_ratchet(repo)
    assert scan.available is False
    assert scan.reason == "no commit history"


def test_scan_degrades_when_accumulate_raises(tmp_path: Path, monkeypatch) -> None:
    """An unexpected error inside the pipeline degrades to available=False.

    The pipeline is wrapped so the deterministic core never crashes its caller.
    Forcing ``_accumulate_history`` to raise must surface as a degraded scan with
    the exception type in the reason, not a propagated traceback.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()
    _commit(repo, "a.py", _lines(10), clock)

    def boom(_repo_top: str) -> dict:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(ar, "_accumulate_history", boom)
    scan = scan_accretion_ratchet(repo)
    assert scan.available is False
    assert "RuntimeError" in scan.reason
    assert "synthetic failure" in scan.reason


# --- internal helpers --------------------------------------------------------

def test_repo_top_resolves_and_rejects_non_repo(tmp_path: Path) -> None:
    """``_repo_top`` returns the toplevel inside a repo, None outside one."""
    repo = _init_repo(tmp_path)
    clock = _Clock()
    _commit(repo, "a.py", _lines(3), clock)
    # Resolves to the repo top from a subdirectory.
    sub = repo / "pkg"
    sub.mkdir()
    top = _repo_top(sub)
    assert top is not None
    assert Path(top).resolve() == repo.resolve()

    plain = tmp_path / "plain"
    plain.mkdir()
    assert _repo_top(plain) is None


def test_is_monotonic_nondecreasing_edge_cases() -> None:
    """Empty / single-point sequences are vacuously monotonic; a dip is not."""
    assert _is_monotonic_nondecreasing([]) is True
    assert _is_monotonic_nondecreasing([5]) is True
    assert _is_monotonic_nondecreasing([1, 1, 2, 3]) is True
    assert _is_monotonic_nondecreasing([1, 2, 1]) is False


def test_build_accretion_file_filters() -> None:
    """The promote-to-AccretionFile filters each reject for the documented reason."""
    # Below the multi-commit gate.
    assert _build_accretion_file(
        "f", _FileHistory(additions=100, deletions=0, commit_count=2,
                          net_sequence=[50, 100]), 0.15) is None
    # Zero total churn (no add, no del) - guarded before the division.
    assert _build_accretion_file(
        "f", _FileHistory(additions=0, deletions=0, commit_count=3,
                          net_sequence=[0, 0, 0]), 0.15) is None
    # Deletion fraction at/over threshold.
    assert _build_accretion_file(
        "f", _FileHistory(additions=80, deletions=20, commit_count=3,
                          net_sequence=[20, 40, 60]), 0.15) is None
    # Net <= 0 with a permissive threshold: a file with equal add/delete churn
    # (fraction 0.5) clears a 0.6 threshold but nets to zero, so the net<=0 guard
    # is what rejects it - distinct from the deletion-fraction filter above.
    assert _build_accretion_file(
        "f", _FileHistory(additions=10, deletions=10, commit_count=3,
                          net_sequence=[0, 0, 0]), 0.6) is None
    # Non-monotonic running net (a dip) clears the flag.
    assert _build_accretion_file(
        "f", _FileHistory(additions=300, deletions=10, commit_count=3,
                          net_sequence=[100, 50, 290]), 0.15) is None
    # A clean accretor passes and carries the right fields.
    ok = _build_accretion_file(
        "f", _FileHistory(additions=300, deletions=10, commit_count=3,
                          first_time=_BASE_EPOCH, last_time=_BASE_EPOCH + _DAY,
                          net_sequence=[100, 200, 290]), 0.15)
    assert ok is not None
    assert ok.net_additions == 290
    assert ok.commit_count == 3


def test_accumulate_history_skips_binary_and_malformed(tmp_path: Path) -> None:
    """``_accumulate_history`` skips binary rows and never indexes them.

    A repo with a binary file and a text file: the returned history map contains
    the text path with real line counts and omits the binary path entirely
    (its numstat rows are ``-``, filtered at parse time).
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()
    (repo / "data.bin").write_bytes(bytes([0, 1, 2, 255] * 50))
    (repo / "code.py").write_text(_lines(40), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1", env=clock.env())

    top = _repo_top(repo)
    assert top is not None
    hist = _accumulate_history(top)
    assert "code.py" in hist
    assert hist["code.py"].additions == 40
    assert "data.bin" not in hist


def test_accumulate_history_on_non_repo_returns_empty(tmp_path: Path) -> None:
    """git log failing (not a repo path) yields an empty history map, no raise."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _accumulate_history(str(plain)) == {}


def test_accumulate_history_skips_malformed_records(monkeypatch) -> None:
    """Malformed commit records are skipped, not allowed to corrupt the map.

    Feeds ``_accumulate_history`` a hand-built git-log payload with three records:
    a header with no SHA (one field -> skipped at the len check), a header whose
    timestamp is non-numeric (ValueError -> skipped), and one well-formed commit.
    Only the good commit's file survives, proving the two parse guards skip rather
    than crash. ``\x1e`` opens each record; rows are ``added\tremoved\tpath``.
    """
    RS = "\x1e"
    payload = (
        f"{RS}1700000000\n"               # header missing the SHA -> len(header)!=2
        "5\t0\torphan.py\n"
        f"{RS}notanumber abc123\n"         # non-numeric author time -> ValueError
        "9\t0\tbadtime.py\n"
        f"{RS}1700100000 deadbeef\n"       # well-formed
        "40\t2\tgood.py\n"
    )

    class _Result:
        stdout = payload

    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _Result())
    hist = _accumulate_history("/irrelevant")
    assert set(hist) == {"good.py"}
    assert hist["good.py"].additions == 40
    assert hist["good.py"].deletions == 2


# --- CLI ---------------------------------------------------------------------

def test_cli_reports_offenders_and_writes_json(tmp_path: Path, capsys, monkeypatch) -> None:
    """``main()`` scans, prints the offender readout, and writes the JSON sidecar.

    Drives the CLI entry point on a real repo with one append-only file. Exit
    code 0 on a successful scan; the JSON file matches the printed summary; the
    offender line for the flagged file appears in stdout.
    """
    repo = _init_repo(tmp_path)
    clock = _Clock()
    for i in range(1, 5):
        _commit(repo, "grow.py", _lines(100 * i), clock)

    out_json = tmp_path / "out.json"
    monkeypatch.setattr(
        "sys.argv",
        ["accretion_ratchet.py", str(repo), "--json", str(out_json)],
    )
    rc = ar.main()
    assert rc == 0

    printed = capsys.readouterr().out
    assert "accreting files:" in printed
    assert "grow.py" in printed

    data = json.loads(out_json.read_text())
    assert data["available"] is True
    assert data["total_accreting"] >= 1
    assert any(f["path"] == "grow.py" for f in data["top_offenders"])
    assert "elapsed_seconds" in data


def test_cli_returns_nonzero_when_unavailable(tmp_path: Path, capsys, monkeypatch) -> None:
    """``main()`` exits non-zero and prints the reason on an unavailable scan."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr("sys.argv", ["accretion_ratchet.py", str(plain)])
    rc = ar.main()
    assert rc == 1
    assert "unavailable" in capsys.readouterr().out
