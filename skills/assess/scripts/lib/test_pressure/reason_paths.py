"""Cut a directory's absolute path out of a tool's output line (issue #964).

The mutmut 3 adapter stores the tail of a failed run's output in
run-context.json, so the scratch copy, its environment and the home directory
must not appear there. A tool can cite a directory with either separator (on
Windows ``str(path)`` uses ``\\`` while pytest and the tool often print ``/``),
so every spelling is cut under both, and what is left of a path reads with
``/`` on every platform.
"""
from __future__ import annotations

import re
from pathlib import Path

# The rest of a cited path: it ends at whitespace, a quote, a bracket or a list separator.
_TAIL = r"[^\s'\"()\[\]<>,;]*"


def _spellings(path: Path) -> list[str]:
    """``path`` as given and resolved, each with ``/`` and with ``\\``, longest first."""
    forms = {str(path), str(path.resolve())}
    every = {f.replace("\\", "/") for f in forms} | {f.replace("/", "\\") for f in forms}
    return sorted(every, key=len, reverse=True)


def cut_prefix(detail: str, path: Path, label: str | None) -> str:
    """Cut ``path`` from ``detail``: with no ``label`` a path under it becomes
    relative and the directory itself ``.``; with one, the prefix becomes the
    label. The prefix is cut only before a separator, so ``/home/u`` does not
    rewrite ``/home/u2``, and the separators in the rest of that path become
    ``/``. Longest spelling first: where the directory sits behind a symlink
    (macOS /var -> /private/var) the unresolved form is a substring of the
    resolved one, and cutting it first would leave "/private" glued on."""
    if label is not None and path == Path(path.anchor):
        return detail  # a root home (HOME=/) would relabel every path separator
    lead = "" if label is None else label + "/"
    for prefix in _spellings(path):
        detail = re.sub(re.escape(prefix) + r"[/\\](" + _TAIL + ")",
                        lambda m: lead + m.group(1).replace("\\", "/"), detail)
        if label is None:
            detail = detail.replace(prefix, ".")
    return detail
