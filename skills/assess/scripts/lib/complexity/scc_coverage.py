"""What scc's file-level complexity covers, and where it cannot be trusted.

scc scores a file the per-function backends cannot read (Shell, Makefile,
TOML, ...) with one keyword count for the whole file and no function
breakdown. ``backend_by_language`` maps each scc language to the per-function
backend that covered it, or to null where scc alone scored it;
``file_level_by_language`` counts, per language, the files scc alone scored.

``complexity_unreliable`` flags the scc-only files whose count is implausibly
low for their size (issue #590). scc's keyword counter loses its place on some
shell quoting - an escaped quote inside a parameter expansion such as
``"${id//\\"/}"`` reads to it as an open string - and then counts no branch for
the rest of the file, so a 2,000-line script can score 5. The check compares
each file's density (ccn per NLOC) with the median density of the scc-only
files of the same language: a large file an order of magnitude below its
language's median is flagged, and the run reports it as an anomaly. Stdlib only.

What scc's file-level complexity covers. `DATA_LANGUAGES` (JSON, YAML, JSONL) splits
code from data; `backend_by_language` maps each scc language to its per-function
backend, or null when no backend scored any file of it and scc counted decision points in
one. Partial coverage reads as the backend (issue #1235): `file_level_by_language` counts
the files of each language with decision points that scc alone scored, so a Python repo
with one shebang script lizard skipped reads `lizard` plus a count of 1, not null.
`complexity_unreliable` flags scc-only code files whose density (ccn per NLOC) is under
`UNRELIABLE_DENSITY_RATIO` (0.1) of their language's median, for files of at least
`MIN_UNRELIABLE_NLOC` (200) in a language with at least two files of
`MIN_MEDIAN_NLOC` (20) or more (issue #590): scc's keyword counter loses its place on
some shell quoting and scores a 2,000-line script near zero. `complexity-treemap.py`
writes the rows (`UnreliableCcnRow`, defined here) as `complexity_unreliable` in the stats sidecar
(`STATS_SCHEMA_VERSION` 7), `run_setup.py` copies them into `stats_summary`,
`anomaly_detector.py` reports `COMPLEXITY_UNRELIABLE`, and `assess_report.py` labels
scc-only CCN cells `~N (approximate)` and flagged ones `~N (unreliable)` (the skills' prose
adds the `ccn` prefix: `ccn ~N (approximate)`, `ccn ~N (unreliable)`). Ranking is
unchanged. Stdlib only. Tests: `tests/test_scc_coverage.py`.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import TypedDict

# scc language names counted as data, not code, for the `max_code` / `max_data`
# split in the stats file. Data files stay in the treemap: a large hand-kept
# fixture is weight an agent may have to read.
DATA_LANGUAGES = frozenset({"JSON", "YAML", "JSONL"})

# A file is flagged when its density is below this share of its language's
# median: an order of magnitude below the files written in the same language.
UNRELIABLE_DENSITY_RATIO = 0.1
# Only files this large are flagged. A short script can be genuinely flat (a
# list of exports, one command with many flags); a long one with no branches is
# what scc's lost-quote state produces.
MIN_UNRELIABLE_NLOC = 200
# Files below this size are left out of the median: their density is noise.
MIN_MEDIAN_NLOC = 20
# A median needs peers: a language scored in a single file is never flagged.
MIN_LANGUAGE_FILES = 2

ScoredFile = tuple[Path, int, float, str]


class UnreliableCcnRow(TypedDict):
    """One ``complexity_unreliable`` row: an scc-only file whose ccn is
    implausibly low for its size."""

    path: str
    language: str
    loc: int
    ccn: float
    density: float
    language_median_density: float


def _file_level_paths(
    files: list[ScoredFile], langs: dict[Path, str], backend_of: dict[Path, str],
) -> list[tuple[Path, str]]:
    """``(path, language)`` for each code file with decision points that scc
    alone scored: no per-function backend read it, so it has no breakdown."""
    out: list[tuple[Path, str]] = []
    for path, _loc, metric, _src in files:
        lang = langs.get(path)
        if lang and path not in backend_of and metric > 0 and lang not in DATA_LANGUAGES:
            out.append((path, lang))
    return out


def backend_by_language(
    files: list[ScoredFile],
    langs: dict[Path, str],
    backend_of: dict[Path, str],
) -> dict[str, str | None]:
    """Map each scc language to its per-function backend, or None.

    A language a backend scored at least one file of maps to that backend,
    even when other files of it fell back to scc (``file_level_by_language``
    counts those); one no backend scored maps to None. See ``write_stats`` in
    complexity-treemap.py for which languages get a key.
    """
    covered: dict[str, str] = {}
    for path, _loc, _metric, _src in files:
        lang = langs.get(path)
        if lang and path in backend_of:
            covered[lang] = backend_of[path]
    uncovered = {lang for _p, lang in _file_level_paths(files, langs, backend_of)}
    return {lang: covered.get(lang) for lang in covered.keys() | uncovered}


def file_level_by_language(
    files: list[ScoredFile],
    langs: dict[Path, str],
    backend_of: dict[Path, str],
) -> dict[str, int]:
    """Count, per scc language, the files with decision points scc alone scored.

    Sorted by language; a language with no such file gets no key, so an empty
    map means every code file with decision points has a per-function figure.
    """
    counts: dict[str, int] = {}
    for _path, lang in _file_level_paths(files, langs, backend_of):
        counts[lang] = counts.get(lang, 0) + 1
    return dict(sorted(counts.items()))


def _scc_only_code(files: list[ScoredFile], langs: dict[Path, str],
                   fn_ccn_by_path: dict[Path, list[float]],
                   ) -> dict[str, list[ScoredFile]]:
    """scc-scored code files with no per-function breakdown, by language."""
    by_lang: dict[str, list[ScoredFile]] = {}
    for f in files:
        path, loc, _ccn, src = f
        lang = langs.get(path)
        if (src != "scc" or path in fn_ccn_by_path or not lang
                or lang in DATA_LANGUAGES or loc < MIN_MEDIAN_NLOC):
            continue
        by_lang.setdefault(lang, []).append(f)
    return by_lang


def complexity_unreliable(
    files: list[ScoredFile],
    langs: dict[Path, str],
    fn_ccn_by_path: dict[Path, list[float]],
    root: Path,
) -> list[UnreliableCcnRow]:
    """The scc-only files whose ccn is implausibly low for their size.

    One row per flagged file, sorted by repository-relative path, carrying the
    file's NLOC, its scc ccn, its density and its language's median density
    (both rounded to 4 places). Empty when nothing is flagged.
    """
    rows: list[UnreliableCcnRow] = []
    for lang, group in _scc_only_code(files, langs, fn_ccn_by_path).items():
        if len(group) < MIN_LANGUAGE_FILES:
            continue
        typical = median(ccn / loc for _p, loc, ccn, _s in group)
        if typical <= 0:
            continue
        for path, loc, ccn, _src in group:
            density = ccn / loc
            if loc >= MIN_UNRELIABLE_NLOC and density < UNRELIABLE_DENSITY_RATIO * typical:
                rows.append({
                    "path": _rel(path, root),
                    "language": lang,
                    "loc": int(loc),
                    "ccn": float(ccn),
                    "density": round(density, 4),
                    "language_median_density": round(typical, 4),
                })
    return sorted(rows, key=lambda r: r["path"])


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
