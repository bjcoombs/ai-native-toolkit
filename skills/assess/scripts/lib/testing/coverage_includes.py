"""A coverage gate the checkout lacks may sit in a GitLab include the coverage scan does not read (issue #1350).

``detect_coverage_gate`` reads gates from the checkout's own files only: the CI file it reads is
the root ``.gitlab-ci.yml``, never an included one, even one ``lib.ci.ci_includes.resolve_includes``
resolved. When it finds no gate (``enforced_basis`` ``no_gate``) and the root ``.gitlab-ci.yml``
names a local, project, remote or component include, or one not parsed, the gate may run there:
``include_hold`` turns that false reading into null with ``enforced_basis``
``ci_includes_unresolved``. A GitLab template is named by its path and holds no project's gate, and
the toolkit's own gate template (status ``toolkit_gate``) runs no coverage, so neither holds a
reading. A gate read in the checkout keeps its reading (a low one stays
``threshold_below_minimum``, an unread one ``threshold_not_read``): the include is not where that
number is. A Go or Rust module with no gate, which reads null (``go_rust_gate_form_not_read``),
takes the include's reason as well.

Inward-only imports: ``lib.ci.ci_includes``; never an orchestrator. Tests:
``tests/test_unresolved_includes.py``.
"""
from __future__ import annotations

from pathlib import Path

from lib.ci.ci_includes import resolve_includes
from lib.testing.coverage_ci import EnforcedBasis

INCLUDES_UNRESOLVED: EnforcedBasis = "ci_includes_unresolved"
# The include kinds that can add a job this run cannot see (as ``lib.secrets_guard`` reads them).
HIDING_KINDS = frozenset({"local", "project", "remote", "component", "unknown"})
TOOLKIT_GATE = "toolkit_gate"  # lib.ci.ci_includes: the toolkit's own gate template, which runs no coverage


def unread_includes(root: Path) -> int:
    """The ``.gitlab-ci.yml`` includes that could hold a coverage job, which the coverage scan never
    reads, resolved or not (only the toolkit's own gate template is settled for it)."""
    return sum(r["status"] != TOOLKIT_GATE and r["kind"] in HIDING_KINDS
               for r in resolve_includes(root).includes)


def include_hold(reading: tuple[bool | None, EnforcedBasis], root: Path,
                 found: bool = False) -> tuple[bool | None, EnforcedBasis]:
    """``reading`` (``enforced_meets_minimum``, ``enforced_basis``), null when no gate was found and
    an unread include could hold one. ``found``: a gate was read in the checkout. With none, a
    ``threshold_not_read`` (a go.mod or Cargo.toml with no gate, ``go_rust_gate_form_not_read``)
    takes the include's reason too; a gate whose threshold is unread keeps its own."""
    if (reading[1] == "no_gate" or (reading[1] == "threshold_not_read" and not found)) and unread_includes(root):
        return None, INCLUDES_UNRESOLVED
    return reading
