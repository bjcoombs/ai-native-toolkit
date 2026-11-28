"""A coverage gate the checkout lacks may sit in a GitLab include this run did not read (issue #1350).

``detect_coverage_gate`` reads gates from the checkout only. When it finds none (``enforced_basis``
``no_gate``) and the root ``.gitlab-ci.yml`` names an include that could hold a job and was not read
(a local, project, remote or component include, or one not parsed, whose status is not settled by
``lib.ci.ci_includes.resolve_includes``), the gate may run there: ``include_hold`` turns that false
reading into null with ``enforced_basis`` ``ci_includes_unresolved``. A GitLab template is read by
its path and holds no project's gate, so it never holds a reading. A gate read in the checkout keeps
its reading (a low one stays ``threshold_below_minimum``): the include is not where that number is.

The collector reads no ghsync root, so a project include always reads unresolved here; the coverage
reader does not read an included file's lines either, so that is the honest reading.

Inward-only imports: ``lib.ci.ci_includes``; never an orchestrator. Tests:
``tests/test_unresolved_includes.py``.
"""
from __future__ import annotations

from pathlib import Path

from lib.ci.ci_includes import SETTLED, resolve_includes
from lib.testing.coverage_ci import EnforcedBasis

INCLUDES_UNRESOLVED: EnforcedBasis = "ci_includes_unresolved"
# The include kinds that can add a job this run cannot see (as ``lib.secrets_guard`` reads them).
HIDING_KINDS = frozenset({"local", "project", "remote", "component", "unknown"})


def unread_includes(root: Path) -> int:
    """The root ``.gitlab-ci.yml``'s includes that could hold a job and were not read."""
    return sum(r["status"] not in SETTLED and r["kind"] in HIDING_KINDS
               for r in resolve_includes(root).includes)


def include_hold(reading: tuple[bool | None, EnforcedBasis], root: Path) -> tuple[bool | None, EnforcedBasis]:
    """``reading`` (``enforced_meets_minimum``, ``enforced_basis``), null when no gate was found and
    an unread include could hold one."""
    if reading[1] == "no_gate" and unread_includes(root):
        return None, INCLUDES_UNRESOLVED
    return reading
