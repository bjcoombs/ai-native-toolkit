"""Cut a directory's absolute path out of a tool's output line (issue #964).

The mutmut 3 adapter stores the tail of a failed run's output in
run-context.json, so the scratch copy, its environment and the home directory
must not appear there. A tool can cite a directory with either separator (on
Windows ``str(path)`` uses ``\\`` while pytest and the tool often print ``/``),
or as a Python repr with each ``\\`` doubled (an ``OSError`` quotes its
filename that way), so every spelling is cut under every separator, and what
is left of a path reads with ``/`` on every platform. Windows paths are
case-insensitive, so there the match ignores case.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# The rest of a cited path: it ends at whitespace, a quote, a bracket or a list separator.
_TAIL = r"[^\s'\"()\[\]<>,;]*"
# ``/``, ``\`` or the doubled ``\\`` of a repr.
_SEP = r"(?:/|\\{1,2})"
# The directory's own last segment has ended: a character that could carry it on
# (``/home/u`` inside ``/home/u2``, ``/home/u.bak`` or ``/home/u-old``) does not follow.
# A ``.`` carries it on only when a word character, ``~`` or ``-`` follows, so the
# full stop that ends a sentence (``No such file: /home/u.``) and an ellipsis
# (``Scanning /home/u...``) are still boundaries.
_SEGMENT_END = r"(?![\w~-]|\.[\w~-])"


def _case_insensitive() -> bool:
    return sys.platform == "win32"


def _spellings(path: Path) -> list[str]:
    """``path`` as given and resolved, each with ``/``, ``\\`` and a repr's
    doubled ``\\``, longest first."""
    forms = {f.replace("\\", "/") for f in (str(path), str(path.resolve()))}
    every = forms | {f.replace("/", "\\") for f in forms} | {f.replace("/", "\\\\") for f in forms}
    return sorted(every, key=len, reverse=True)


def cut_prefix(detail: str, path: Path, label: str | None) -> str:
    """Cut ``path`` from ``detail``: with no ``label`` a path under it becomes
    relative and the directory itself ``.``; with one, the prefix becomes the
    label. A labelled prefix is cut only where its last segment ends (before a
    separator, at the end of the text, or before any other character that
    cannot carry a path segment on), so ``/home/u`` does not rewrite
    ``/home/u2`` while a bare ``HOME=/home/u`` reads ``HOME=~`` (issue #978),
    and the separators in the rest of that path become ``/``. Longest spelling
    first: where the directory sits behind a symlink
    (macOS /var -> /private/var) the unresolved form is a substring of the
    resolved one, and cutting it first would leave "/private" glued on."""
    if label is not None and path == Path(path.anchor):
        return detail  # a root home (HOME=/) would relabel every path separator
    lead = "" if label is None else label + "/"
    flags = re.IGNORECASE if _case_insensitive() else 0
    for prefix in _spellings(path):
        detail = re.sub(re.escape(prefix) + _SEP + "(" + _TAIL + ")",
                        lambda m: lead + re.sub(r"\\{1,2}", "/", m.group(1)),
                        detail, flags=flags)
        bare = "." if label is None else label
        tail = "" if label is None else _SEGMENT_END
        detail = re.sub(re.escape(prefix) + tail, lambda _m: bare, detail, flags=flags)
    return detail
