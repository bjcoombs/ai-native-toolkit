"""What scc's file-level complexity covers, and where it cannot be trusted.

scc scores a file the per-function backends cannot read (Shell, Makefile,
TOML, ...) with one keyword count for the whole file and no function
breakdown. ``backend_by_language`` maps each scc language to the per-function
backend that covered it, or to null where scc alone scored it.

``complexity_unreliable`` flags the scc-only files whose count is implausibly
low for their size (issue #590). scc's keyword counter loses its place on some
shell quoting - an escaped quote inside a parameter expansion such as
``"${id//\\"/}"`` reads to it as an open string - and then counts no branch for
the rest of the file, so a 2,000-line script can score 5. The check compares
each file's density (ccn per NLOC) with the median density of the scc-only
files of the same language: a large file an order of magnitude below its
language's median is flagged, and the run reports it as an anomaly. Stdlib only.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median

from lib.run_context_types import UnreliableCcnRow

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


def backend_by_language(
    files: list[ScoredFile],
    langs: dict[Path, str],
    backend_of: dict[Path, str],
) -> dict[str, str | None]:
    """Map each scc language to its per-function backend, or None.

    See ``write_stats`` in complexity-treemap.py for which languages get a key.
    """
    covered: dict[str, str] = {}
    uncovered: set[str] = set()
    for path, _loc, metric, _src in files:
        lang = langs.get(path)
        if not lang:
            continue
        if path in backend_of:
            covered[lang] = backend_of[path]
        elif metric > 0 and lang not in DATA_LANGUAGES:
            uncovered.add(lang)
    # A language counts as covered only when no file of it with decision points
    # fell back to scc: partial coverage reads as null, not as the backend.
    return {
        lang: (None if lang in uncovered else covered[lang])
        for lang in covered.keys() | uncovered
    }


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
