"""B3 static-vs-historical disagreement: the A x B cross for the /assess core.

The static lens (``lib.structure_graph``, A2/A3) reads the import graph and asks
"does this *look* modular?"; the historical lens (``lib.change_coupling``, B1/B2)
reads the commit log and asks "does it *behave* modularly -- do edits stay
contained?". Each is blind to what the other sees. Where they **disagree** is
the most valuable output of the whole assessment (PRD Signal B3 + the derived-
findings table):

  - **Hidden coupling** -- a module that looks modular statically (high
    modularity / a clean front door) yet bleeds historically (low containment:
    its commits keep dragging in files elsewhere). The static boundary is lying;
    recommend *investigating the seam* before trusting it.
  - **Bleeding module** -- the v1 graceful fallback. When no static graph is
    available (a non-Python repo, or grimp/networkx absent) we have only the
    historical lens, so a low-containment module is flagged on history alone
    rather than cross-checked against a (missing) static boundary.
  - **Looks-coupled-but-never-co-changes** -- the inverse disagreement. The
    static graph says "coupled" but history shows the edits stay contained. This
    is fine in practice, so it is **suppressed** (``finding=None``): the static
    graph already surfaces the coupling via its SCCs / burrow edges, and there
    is no behavioural bleed to act on.
  - **Refactor boundary** *(the one positive finding)* -- high containment plus
    low external coupling: empirically, edits here stay put. This is the direct
    yes to the assessment's core question -- *can an agent safely change this
    area through a keyhole?* -- so these are surfaced as safe zones.

This module is a pure function of its inputs (containment ratios + an optional
per-directory static-modularity view); it runs no git or grimp itself. That
keeps it cheap to test (mock the two inputs) and lets task #5 wire the real
``containment_by_dir`` / ``change_coupling_pairs`` / ``analyze_structure``
outputs into the run-context ``behaviour`` block. All returned structures are
JSON-serialisable (paths as strings, plain dict/list/number/None).

**Static-modularity input shape.** ``static_modularity`` maps a directory path
(matching the keys of ``containment_by_dir``) to a metrics dict
``{'modularity_q': float | None, 'front_door_ratio': float | None}``. Because
``lib.structure_graph`` currently emits repo-level ``modularity_q`` /
``front_door_ratio``, the caller (task #5) is responsible for projecting those
onto the directories it cares about; this module only consumes the per-directory
view. ``None`` for the whole argument means "no static graph at all" -> the
graceful historical-only path. A directory absent from an otherwise-present dict
is treated the same way per-directory (no static evidence for *that* dir).
"""
from __future__ import annotations

# Containment below this fraction means a module bleeds: most of its commits
# drag in files outside it. The PRD leaves the exact island threshold open for
# calibration (Open Question: "what containment ratio counts as an island?");
# 0.3 / 0.7 are the v1 defaults and are overridable per call.
DEFAULT_LOW_CONTAINMENT = 0.3
DEFAULT_HIGH_CONTAINMENT = 0.7

# A directory "looks modular" statically when EITHER its modularity is high
# (cohesive clusters, sparse cross-talk -- A2) OR most inbound cross-package
# edges hit its front door rather than burrowing into internals (a real
# contract -- A3). Either clean-boundary signal is enough to make the static map
# *claim* modularity; when history then contradicts it, that claim is the lie
# worth investigating. OR (not AND) is deliberate: it makes hidden-coupling
# detection more sensitive, erring toward surfacing a seam for human review.
DEFAULT_HIGH_MODULARITY_Q = 0.3
DEFAULT_HIGH_FRONT_DOOR_RATIO = 0.7


def _looks_modular(
    metrics: dict | None,
    high_modularity_q: float,
    high_front_door_ratio: float,
) -> bool:
    """True if a directory's static metrics present a clean (modular) boundary.

    ``metrics`` is ``{'modularity_q': float | None, 'front_door_ratio': float |
    None}`` (or ``None`` when there is no static evidence for the directory).
    Modular = high modularity OR high front-door ratio; ``None`` sub-metrics are
    simply skipped, so a dict carrying only one of the two still works.
    """
    if not metrics:
        return False
    q = metrics.get("modularity_q")
    fd = metrics.get("front_door_ratio")
    if q is not None and q >= high_modularity_q:
        return True
    if fd is not None and fd >= high_front_door_ratio:
        return True
    return False


def detect_hidden_coupling(
    containment_by_dir: dict[str, float],
    static_modularity: dict | None = None,
    threshold_low_containment: float = DEFAULT_LOW_CONTAINMENT,
    high_modularity_q: float = DEFAULT_HIGH_MODULARITY_Q,
    high_front_door_ratio: float = DEFAULT_HIGH_FRONT_DOOR_RATIO,
) -> list[dict]:
    """B3: cross static modularity with historical containment to find lying boundaries.

    For each directory in ``containment_by_dir`` whose containment is **below**
    ``threshold_low_containment`` (it bleeds historically), classify the bleed
    against the static lens:

      - static graph present *and* the directory looks modular -> ``'hidden_coupling'``
        (the static boundary is lying; recommend investigating the seam),
      - no static graph at all (``static_modularity is None``) or no static
        evidence for this directory -> ``'bleeding_module'`` (historical-only
        fallback),
      - static graph present *and* the directory also looks coupled -> ``None``
        (static and history agree it is coupled; not *hidden*, and already
        visible in the static SCC / burrow-edge output -- nothing new to flag).

    Directories that do **not** bleed (containment at or above the threshold) are
    not the concern of this function -- the inverse "looks-coupled-but-never-co-
    changes" case is suppressed here and the positive case is handled by
    :func:`find_refactor_boundaries` -- so they are omitted from the result.

    Returns a list of ``{'path', 'containment_ratio', 'finding', 'recommendation'}``
    sorted by containment ascending (worst bleed first), then path. ``finding``
    is ``'hidden_coupling'``, ``'bleeding_module'`` or ``None`` (suppressed but
    reported, so a caller can see the directory was evaluated and consciously
    left alone).
    """
    results: list[dict] = []
    for path in sorted(containment_by_dir):
        containment = containment_by_dir[path]
        if containment >= threshold_low_containment:
            continue  # does not bleed: not this function's concern

        metrics = static_modularity.get(path) if static_modularity is not None else None
        has_static_evidence = static_modularity is not None and metrics is not None

        if has_static_evidence:
            if _looks_modular(metrics, high_modularity_q, high_front_door_ratio):
                finding: str | None = "hidden_coupling"
                recommendation = (
                    "investigate the seam - the static boundary looks modular but "
                    "its commits bleed outside it; the boundary is lying"
                )
            else:
                # Static and history agree this is coupled. Not hidden, and the
                # static SCC / burrow-edge output already surfaces it.
                finding = None
                recommendation = (
                    "static and historical lenses agree this is coupled; already "
                    "visible in the static structure output, nothing hidden to flag"
                )
        else:
            finding = "bleeding_module"
            recommendation = (
                "edits here bleed outside the directory (low containment); no "
                "static import graph available to cross-check the boundary"
            )

        results.append(
            {
                "path": path,
                "containment_ratio": containment,
                "finding": finding,
                "recommendation": recommendation,
            }
        )

    results.sort(key=lambda d: (d["containment_ratio"], d["path"]))
    return results


def find_refactor_boundaries(
    containment_by_dir: dict[str, float],
    threshold_high_containment: float = DEFAULT_HIGH_CONTAINMENT,
    static_modularity: dict | None = None,
    high_modularity_q: float = DEFAULT_HIGH_MODULARITY_Q,
    high_front_door_ratio: float = DEFAULT_HIGH_FRONT_DOOR_RATIO,
) -> list[dict]:
    """B3 positive finding: directories an agent can safely refactor in isolation.

    A directory whose containment is **above** ``threshold_high_containment`` is
    a refactor boundary: empirically its edits stay put, so it answers the
    assessment's core question -- *can an agent safely change this through a
    keyhole?* -- with a yes. Containment (B2) is itself the direct
    low-external-coupling signal, so high containment alone qualifies a boundary
    even with no static graph (the v1 graceful path).

    ``static_modularity``, when present, only *enriches* the recommendation: a
    boundary that also looks modular statically gets a stronger "static and
    historical lenses agree" note, while one that looks coupled statically still
    qualifies (it never co-changes in practice -- the benign inverse-disagreement
    case) but is noted as historically-backed only. It never disqualifies a
    boundary, because lived behaviour (containment) outranks the static guess.

    Returns ``{'path', 'containment_ratio', 'finding': 'refactor_boundary',
    'recommendation'}`` entries, sorted by containment descending (safest first),
    then path.
    """
    results: list[dict] = []
    for path in sorted(containment_by_dir):
        containment = containment_by_dir[path]
        if containment <= threshold_high_containment:
            continue

        metrics = static_modularity.get(path) if static_modularity is not None else None
        if metrics is not None and _looks_modular(
            metrics, high_modularity_q, high_front_door_ratio
        ):
            recommendation = (
                "safe to hand an agent in isolation - high containment and a "
                "clean static boundary agree this area does not bleed"
            )
        else:
            recommendation = (
                "safe to hand an agent in isolation - edits here stay contained "
                "(high containment)"
            )

        results.append(
            {
                "path": path,
                "containment_ratio": containment,
                "finding": "refactor_boundary",
                "recommendation": recommendation,
            }
        )

    results.sort(key=lambda d: (-d["containment_ratio"], d["path"]))
    return results
