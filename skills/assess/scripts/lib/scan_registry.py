"""Declared table of run-context scans, and the loop that runs them.

``assess_core.build_run_context`` grew by one import and one assignment per
scan until it was the most complex function in the repository, and the "a
broken scan must never block the assessment" rule held only where the author
remembered to wrap the call. Here a scan is declared once - its run-context
key, its callable, what it reads, how it degrades - and one loop runs the
table, so the degrade wrapper applies by construction and adding a scan does
not edit ``build_run_context``. The shape follows ``_DEAD_CODE_TOOLS`` in
``liveness_scan``: spec entries plus a single driving loop.

The table is validated when this module is imported. A duplicate key, an unknown
stage, a read of a name that neither the core provides nor an earlier scan
produces, a read of a key produced at a later stage, a callable that cannot take
as many positional arguments as it declares reads, or a scan that opts out of
degrading without a ``gate_reason`` raises ``ScanRegistryError`` before any run
starts.

A spec publishes in one of three ways. By default its value is
``ctx[key]``. A ``private`` spec's value is an intermediate (the complexity
stats, the stats diff, a raw scan result) that later specs read but
``run-context.json`` never carries; it is kept in the inputs mapping. A
``spread`` spec returns a mapping whose keys, all named in ``spread``, are
copied into the run context in the order the callable returns them - one entry
for a call that yields several blocks, such as the run header or the keyhole
integration. Its value is also kept under ``key`` for later specs to read.

``stage`` names the point in ``build_run_context`` where an entry runs, which
keeps the key order of ``run-context.json`` unchanged. The core still assigns
the ``HAND_WIRED`` keys by hand between ``STAGE_SETUP`` and
``STAGE_READ_SIDE`` (the doc-staleness block, held back for the 2.0.0 rewrite
in issues #424 and #425); the stages collapse once those are gone.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from lib import core_scans as cs
from lib import run_setup as rs
from lib.agent_environment import scan_agent_environment
from lib.agent_harness import scan_agent_harness
from lib.agent_ops import scan_agent_ops
from lib.ai_usage import scan_ai_usage
from lib.claude_config import scan_claude_config
from lib.config_drift import scan_config_drift
from lib.degrade import safe as safe  # re-exported: callers import it from here
from lib.degrade import unavailable
from lib.coverage_gate import detect_coverage_gate
from lib.doc_claims import scan_doc_claims
from lib.gate_cost import estimate_gate_cost
from lib.instruction_claims import scan_instruction_claims
from lib.instruction_files import nested_instruction_surface
from lib.instruction_placement import scan_instruction_placement
from lib.review_reality import scan_review_reality
from lib.shared_tooling import scan_shared_tooling

# Names the core passes to ``run_scans``: the arguments of
# ``build_run_context``. A spec may read these, or the key of any spec declared
# before it (``instruction_files``, ``scope``, ``excludes`` and ``history_window``
# are setup specs).
PROVIDED_INPUTS = frozenset({"repo_root", "requested_scope", "run_date", "non_interactive"})

STAGE_SETUP = "setup"
STAGE_READ_SIDE = "read_side"
STAGE_POST_OFFERS = "post_offers"
STAGE_FINAL = "final"
STAGES = (STAGE_SETUP, STAGE_READ_SIDE, STAGE_POST_OFFERS, STAGE_FINAL)

# Run-context keys the core still assigns by hand, and the first stage that may
# read them. Held back for child B of issue #418 (#424 and #425 rewrite them).
HAND_WIRED: Mapping[str, str] = {
    "doc_staleness": STAGE_READ_SIDE,
    "stale_hubs": STAGE_READ_SIDE,
    "churn_degenerate": STAGE_READ_SIDE,
}


class ScanRegistryError(ValueError):
    """The scan table is malformed (duplicate key, unknown stage or read)."""


@dataclass(frozen=True)
class ScanSpec:
    """One run-context block.

    ``fn`` is called with the values of ``reads``, positionally and in order.
    ``degrade`` routes the call through ``safe``; a scan opts out only by
    setting ``gate_reason``, because a gate is the one kind of scan whose
    failure should stop the run. ``label`` names the scan in a degrade reason
    when it differs from ``key``. ``private`` and ``spread`` are described in
    the module docstring; a degraded spread spec marks every key it declares.

    ``network`` marks a scan that reads beyond the checkout (GitHub via
    ``gh``). The headless signals runner (``lib/signals.py``) never calls such
    a scan's ``fn``: it calls ``offline_fn`` when one is declared - the same
    scan with its network probe turned off, taking the same reads - and
    otherwise reports the scan unavailable. ``assess_core`` ignores both fields.
    """

    key: str
    fn: Callable[..., Any]
    reads: tuple[str, ...]
    stage: str
    degrade: bool = True
    gate_reason: str = ""
    label: str = ""
    private: bool = False
    spread: tuple[str, ...] = ()
    network: bool = False
    offline_fn: Callable[..., Any] | None = None


def _accepts(fn: Callable[..., Any], count: int) -> bool:
    """True when ``fn`` can be called with ``count`` positional arguments.

    A callable whose signature cannot be inspected is given the benefit of the
    doubt; the mismatch this guards against is a declared table entry, which is
    always a plain function.
    """
    try:
        inspect.signature(fn).bind(*[None] * count)
    except TypeError:
        return False
    except ValueError:
        return True
    return True


def validate(
    specs: Iterable[ScanSpec], provided: Iterable[str] = PROVIDED_INPUTS,
    hand_wired: Mapping[str, str] = HAND_WIRED,
) -> None:
    """Raise ``ScanRegistryError`` unless every spec can run in table order.

    Stages run in ``STAGES`` order, so a read must name a provided input, a
    hand-wired key the core has assigned by the reading stage, or a key whose
    producer is declared earlier *and* runs at the same or an earlier stage.
    The run-context keys the table writes (published keys and spread keys) must
    be distinct from each other and from the hand-wired keys.
    """
    produced_at: dict[str, int] = {name: -1 for name in provided}
    produced_at.update({name: STAGES.index(stage) for name, stage in hand_wired.items()})
    written: set[str] = set(hand_wired)
    for spec in specs:
        if spec.key in produced_at:
            raise ScanRegistryError(f"scan {spec.key!r} is declared twice or shadows a provided input")
        if spec.stage not in STAGES:
            raise ScanRegistryError(f"scan {spec.key!r} names unknown stage {spec.stage!r}")
        stage_index = STAGES.index(spec.stage)
        missing = [name for name in spec.reads if name not in produced_at]
        if missing:
            raise ScanRegistryError(
                f"scan {spec.key!r} reads {missing}, which no earlier scan produces "
                f"and the core does not provide"
            )
        late = [name for name in spec.reads if produced_at[name] > stage_index]
        if late:
            raise ScanRegistryError(
                f"scan {spec.key!r} runs at stage {spec.stage!r} but reads {late}, "
                f"produced at a later stage"
            )
        if not _accepts(spec.fn, len(spec.reads)):
            raise ScanRegistryError(
                f"scan {spec.key!r} declares {len(spec.reads)} read(s) but its callable "
                f"cannot be called with that many positional arguments"
            )
        if spec.offline_fn is not None and not spec.network:
            raise ScanRegistryError(f"scan {spec.key!r} declares an offline_fn but is not a network scan")
        if spec.offline_fn is not None and not _accepts(spec.offline_fn, len(spec.reads)):
            raise ScanRegistryError(
                f"scan {spec.key!r} declares {len(spec.reads)} read(s) but its offline_fn "
                f"cannot be called with that many positional arguments"
            )
        if not spec.degrade and not spec.gate_reason:
            raise ScanRegistryError(f"scan {spec.key!r} opts out of degrading without a gate_reason")
        if spec.private and spec.spread:
            raise ScanRegistryError(f"scan {spec.key!r} is both private and spread")
        keys = spec.spread if spec.spread else (() if spec.private else (spec.key,))
        clash = sorted({k for k in keys if k in written} | {k for k in keys if keys.count(k) > 1})
        if clash:
            raise ScanRegistryError(f"scan {spec.key!r} writes run-context key(s) {clash} written elsewhere")
        written.update(keys)
        produced_at[spec.key] = stage_index


def _resolve(spec: ScanSpec, name: str, inputs: Mapping[str, Any], ctx: Mapping[str, Any]) -> Any:
    if name in inputs:
        return inputs[name]
    if name in ctx:
        return ctx[name]
    raise ScanRegistryError(
        f"scan {spec.key!r} reads {name!r}, which is neither in the inputs the core "
        f"passed nor in the run context yet"
    )


def _call(spec: ScanSpec, args: tuple[Any, ...]) -> tuple[Any, bool]:
    """``(value, degraded)``: the spec's result, or ``safe``'s marker on failure."""
    if not spec.degrade:
        return spec.fn(*args), False
    try:
        return spec.fn(*args), False
    except Exception as e:  # noqa: BLE001 - intentional catch-all; degrade, don't crash
        return unavailable(spec.label or spec.key, e), True


def _spread(spec: ScanSpec, ctx: dict[str, Any], value: Any, degraded: bool) -> None:
    if degraded:
        for name in spec.spread:
            ctx[name] = value
        return
    if not isinstance(value, Mapping):
        raise ScanRegistryError(f"spread scan {spec.key!r} returned {type(value).__name__}, not a mapping")
    undeclared = [name for name in value if name not in spec.spread]
    if undeclared:
        raise ScanRegistryError(f"spread scan {spec.key!r} returned undeclared key(s) {undeclared}")
    ctx.update(value)


def run_scans(
    ctx: dict[str, Any],
    inputs: MutableMapping[str, Any],
    stage: str,
    specs: Iterable[ScanSpec] | None = None,
) -> None:
    """Run every spec of ``stage`` in table order, writing its result.

    A published spec's value is assigned to ``ctx[spec.key]``; a private spec's
    value is stored in ``inputs[spec.key]``; a spread spec's mapping is copied
    into ``ctx`` and also stored in ``inputs[spec.key]``. A read resolves
    against ``inputs`` first, then against ``ctx``. Reads are resolved outside
    the degrade wrapper: an unresolvable read is a wiring error in the table or
    the core, so it raises ``ScanRegistryError`` and stops the run, while a
    failure inside the scan itself degrades.
    """
    for spec in SCANS if specs is None else specs:
        if spec.stage != stage:
            continue
        args = tuple(_resolve(spec, name, inputs, ctx) for name in spec.reads)
        value, degraded = _call(spec, args)
        if spec.spread:
            _spread(spec, ctx, value, degraded)
            inputs[spec.key] = value
        elif spec.private:
            inputs[spec.key] = value
        else:
            ctx[spec.key] = value


# Gate reasons for entries that ran outside the degrade wrapper before they
# moved into the table, and still do.
_SETUP = (
    "run setup: later entries and the publish step read this value, so a "
    "failure stops the run rather than feeding them an unavailable marker"
)
_BAD_SCOPE = "an invalid --scope is a usage error the CLI reports with exit code 2"
_SELF_DEGRADING = "degrades internally to an explicit unavailable result; a raise here is a bug"
_UNWRAPPED = (
    "assigned outside the degrade wrapper before the migration; a marker dict in "
    "place of this block's list, string or capped shape would mislead its readers"
)
_SERIALISER = "copies fields out of an earlier result that already carries its own degrade"

KEYHOLE_KEYS = (
    "structure", "behaviour", "documentation", "understanding", "runtime",
    "derived_findings", "attention", "attention_low_signal", "findings_markdown",
    "keyhole_summary", "prescribed_actions",
)


SCANS: tuple[ScanSpec, ...] = (
    # -- STAGE_SETUP: the run's own state, then the run header ---------------
    # Every value a later entry or the publish step needs: a failure here
    # stops the run, as it did before these steps moved into the table.
    ScanSpec("run_scope", rs.run_scope, ("repo_root", "requested_scope"), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_BAD_SCOPE),
    ScanSpec("scope", rs.scope_root, ("run_scope",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("assess_dir", rs.run_assess_dir, ("repo_root", "run_scope"), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    # Minted once and stamped on every artifact of this run, so finalize can
    # prove the finalize-input it later consumes matches this run-context.
    ScanSpec("run_id", rs.new_run_id, (), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("current", rs.current_stats, ("assess_dir",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("prior", rs.prior_stats, ("assess_dir",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("excluded_as_generated", rs.generated_paths, ("current",), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    ScanSpec("diff", rs.stats_diff, ("prior", "current", "excluded_as_generated"), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    ScanSpec("instruction_grading", rs.instruction_grading, ("repo_root",), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    ScanSpec("instruction_files", rs.graded_instruction_files, ("instruction_grading",),
             STAGE_SETUP, private=True, degrade=False, gate_reason=_SETUP),
    # Historical path -> current path from git's rename detection, built once:
    # it re-keys the first-flagged map and folds co-change history in keyhole.
    ScanSpec("rename_map", rs.rename_map, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("first_flagged", rs.first_flagged, ("assess_dir", "rename_map"), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    # `.assess/config.toml` excludes, loaded once and read by every scan.
    ScanSpec("excludes", rs.config_excludes, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    # The git-history window a history-reading scan honours: the stats
    # sidecar's churn window in the `<n>d|w|mo|y` grammar, or None for all
    # history. The headless signals run provides `--window` instead.
    ScanSpec("history_window", rs.history_window, ("current",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("measured_commit", rs.measured_commit, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SETUP),
    ScanSpec("superseded", rs.superseded_run, ("assess_dir", "run_date", "measured_commit"),
             STAGE_SETUP, private=True, degrade=False, gate_reason=_SETUP),
    # Files first flagged only by a never-finalized run and now excluded (#356).
    ScanSpec(
        "unfinalized", rs.unfinalized_exclusions,
        ("assess_dir", "superseded", "first_flagged", "current", "diff", "excludes"),
        STAGE_SETUP, private=True, degrade=False, gate_reason=_SETUP,
    ),
    # Promissory markers, scanned here because the hotspot pages carry each
    # file's marker debt; published later as promissory_markers.
    ScanSpec("promissory", cs.promissory_scan, ("repo_root", "excludes", "scope"), STAGE_SETUP,
             private=True, label="promissory_markers"),
    # Accretion ratchet over every file: never raises (it degrades to
    # available=False itself) and returns a dataclass, not a dict.
    ScanSpec("accretion_scan", cs.accretion_scan, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SELF_DEGRADING),
    ScanSpec("accretion_by_file", cs.accretion_by_file, ("accretion_scan",), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    # One repository test index, shared by test_focus and the hotspot pages.
    ScanSpec("hot_test_index", cs.hot_test_index, ("repo_root", "current"), STAGE_SETUP,
             private=True, degrade=False, gate_reason=_SETUP),
    # The first 25 run-context keys; the four wiki keys are filled after the
    # wiki is written (lib/run_publish.py).
    ScanSpec(
        "run_header", rs.run_header,
        ("run_id", "run_date", "run_scope", "measured_commit", "current", "prior",
         "instruction_grading", "diff", "unfinalized"),
        STAGE_SETUP, degrade=False, gate_reason=_SETUP, spread=rs.RUN_HEADER_KEYS,
    ),
    ScanSpec("working_notes", cs.working_notes, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SELF_DEGRADING),
    # Layer 0 navigability: the doc graph. The core then assigns the
    # HAND_WIRED doc-staleness keys before STAGE_READ_SIDE.
    ScanSpec("doc_graph", cs.doc_graph, ("repo_root", "excludes", "scope", "working_notes"),
             STAGE_SETUP),
    # -- STAGE_READ_SIDE ------------------------------------------------------
    # Instruction-surface integrity (Layer 0): uncommitted instruction files,
    # broken instruction references, and redacted sensitive content (#56).
    ScanSpec("untracked_instruction_files", cs.untracked_instruction_files,
             ("instruction_grading",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    ScanSpec("broken_instruction_refs", cs.instruction_refs,
             ("doc_graph", "instruction_grading"), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    ScanSpec("sensitive_instruction_content", cs.sensitive_instruction_content,
             ("instruction_grading",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Software repo or knowledge base (#224); a knowledge base marks L2-L7 N/A.
    ScanSpec("archetype", cs.archetype, ("repo_root",), STAGE_READ_SIDE),
    # Instruction files above the repo root that reach no fresh clone (#57).
    ScanSpec("ancestor_instruction_files", cs.ancestor_instructions, ("repo_root",),
             STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Progressive disclosure: skills factoring and oversized instruction files.
    ScanSpec("skills", cs.skills_blocks, ("instruction_grading",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER,
             spread=("skills_present", "skills_count", "skill_files")),
    ScanSpec("instruction_file_size", cs.instruction_sizes, ("instruction_files",),
             STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Layer 1 liveness. A failed scan yields explicit unavailable blocks, never
    # "no observability"; capability keys appear only when a language is found.
    ScanSpec("liveness", cs.liveness_scan, ("repo_root", "excludes", "scope"), STAGE_READ_SIDE,
             private=True),
    ScanSpec("liveness_blocks", cs.liveness_blocks, ("liveness",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER,
             spread=("dead_code", "observability", "capability_offers", "language_capabilities")),
    ScanSpec("structure_scan", cs.structure_scan, ("repo_root", "excludes", "scope"),
             STAGE_READ_SIDE, private=True, label="structure"),
    # Line coverage from a report the project already generated; /assess never
    # runs the suite. None when absent or malformed.
    ScanSpec("coverage_data", cs.coverage_data, ("repo_root",), STAGE_READ_SIDE, private=True,
             degrade=False, gate_reason=_SELF_DEGRADING),
    ScanSpec("coverage_report", cs.coverage_report, ("repo_root", "coverage_data"),
             STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Layer 1 write-side truth pressure, read-only (the mutation pass is off).
    ScanSpec("test_pressure_scan", cs.test_pressure_scan,
             ("repo_root", "current", "coverage_data"), STAGE_READ_SIDE,
             private=True, label="test_pressure"),
    ScanSpec("test_pressure", cs.test_pressure_block, ("test_pressure_scan",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Layer 6 cap while the mutation tier has not run; assess_finalize enforces it.
    ScanSpec("mutation_not_run_cap", cs.mutation_cap, ("test_pressure",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    ScanSpec("test_focus", cs.test_focus,
             ("repo_root", "current", "coverage_data", "test_pressure", "hot_test_index"),
             STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    ScanSpec("promissory_markers", cs.promissory_block, ("promissory",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Agent-operations guardrails (permission allowlists, hooks, sandbox rules,
    # routine definitions): Layer 8 workflow-maturity evidence. Tracked-only
    # credit - an uncommitted settings file reaches no clone. Deliberately
    # excludes .claude/agents/ and .claude/skills/ (Layer 0's evidence) so the
    # two layers never double-count the same artifact.
    ScanSpec("agent_ops", scan_agent_ops, ("repo_root",), STAGE_READ_SIDE),
    # Configuration drift (Layer 5 lying signal): tracked ruleset and
    # branch-protection snapshots diffed against the live GitHub setting via
    # `gh`. Optional: no remote, no `gh`, no auth or a refused read degrades to
    # available: false with the reason, never a clean result.
    ScanSpec("config_drift", scan_config_drift, ("repo_root",), STAGE_READ_SIDE, network=True),
    ScanSpec("review_reality", scan_review_reality, ("repo_root",), STAGE_READ_SIDE, network=True),
    ScanSpec("gate_cost_estimate", estimate_gate_cost, ("repo_root",), STAGE_READ_SIDE, network=True),
    # Layer 6: is a coverage threshold *enforced*, not just measured? Each gate
    # names its file, line and threshold as written; none found reads
    # enforced: false, never inferred from coverage_report. A scoped run reads
    # the scope plus the root CI and task-runner files (issue #488).
    ScanSpec(
        "coverage_gate", detect_coverage_gate,
        ("repo_root", "scope", "excludes"), STAGE_READ_SIDE,
    ),
    # Accretion filtered to files already in the top complexity/size band.
    ScanSpec("accretion_ratchet", cs.accretion_ratchet, ("accretion_scan", "current"),
             STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Keyhole readiness: structure, behaviour, documentation, understanding and
    # runtime blocks, derived findings, attention and the report products.
    # integrate() wraps each block in its own catch-all.
    ScanSpec(
        "keyhole", cs.keyhole,
        ("repo_root", "current", "doc_staleness", "liveness_blocks", "structure_scan",
         "test_pressure", "promissory", "accretion_ratchet", "archetype", "excludes", "scope",
         "rename_map"),
        STAGE_READ_SIDE, private=True, degrade=False, gate_reason=_SELF_DEGRADING,
    ),
    ScanSpec("keyhole_blocks", cs.keyhole_blocks, ("keyhole",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER, spread=KEYHOLE_KEYS),
    ScanSpec("gap_actions", cs.gap_actions,
             ("coverage_report", "doc_graph", "current", "archetype"), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    ScanSpec("exclusion_disclosures", cs.exclusion_disclosures, ("keyhole", "excludes"),
             STAGE_READ_SIDE, degrade=False, gate_reason=_SERIALISER,
             spread=("excluded_by_config", "excluded_as_archive", "pruned_finding_paths")),
    ScanSpec("excluded_generated", cs.generated_exclusions, ("current",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Declared ownership map vs where the code lives; omitted with no map.
    ScanSpec("structure_drift_block", cs.structure_drift, ("repo_root", "keyhole"),
             STAGE_READ_SIDE, degrade=False, gate_reason=_SELF_DEGRADING,
             spread=("structure_drift",)),
    ScanSpec("plugin_version", rs.read_plugin_version, (), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # Active permanent declines of the optional tools (.assess/.no-<tool>).
    ScanSpec("decline", cs.decline_blocks, ("assess_dir", "plugin_version"), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER,
             spread=("decline_markers", "reoffer_mutation", "decline_disclosures")),
    # Headless runs pre-record every offer as skipped; never inferred from stdin.
    ScanSpec("offers_block", cs.offers_blocks, ("non_interactive",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER, spread=("interactive", "offers")),
    ScanSpec("uninstall_instructions_path", cs.uninstall_instructions_path, (), STAGE_READ_SIDE,
             degrade=False, gate_reason=_UNWRAPPED),
    # -- STAGE_FINAL: last key of run-context.json ----------------------------
    ScanSpec("anomalies", cs.anomalies, ("run_header",), STAGE_FINAL,
             degrade=False, gate_reason=_UNWRAPPED),
    # -- STAGE_POST_OFFERS ----------------------------------------------------
    # Checkable claims in the graded instruction files ("`x.sh` is enforced in
    # CI", "Node 20.11.0 is pinned in `.nvmrc`"), verified against the repo; a
    # failed claim is a Layer 0 lying signal. Zeros when none.
    ScanSpec(
        "instruction_claims", scan_instruction_claims,
        ("repo_root", "instruction_files"), STAGE_POST_OFFERS,
    ),
    # Claude Code configuration (Layer 0): legacy command files and skill,
    # command and agent frontmatter the runtime silently ignores, checked
    # against the documented field sets pinned in lib/claude_config_fields.py.
    # available: false when the run root has no .claude/ and no plugin
    # manifest. Last in the table so every earlier key keeps its position.
    ScanSpec(
        "claude_config", scan_claude_config,
        ("repo_root", "scope", "excludes"), STAGE_POST_OFFERS,
    ),
    # Nested and path-scoped instruction files (Layer 0, issue #511): nested
    # CLAUDE.md / AGENTS.md / GEMINI.md, .claude/rules, Copilot and Cursor
    # rules, each graded with its scope; the always-loaded budget per tool;
    # and dead scope globs, ignored .cursor .md files, a CLAUDE.md shadowing a
    # different AGENTS.md, and nested files that repeat their parent.
    ScanSpec(
        "nested_instructions", nested_instruction_surface,
        ("repo_root", "excludes"), STAGE_POST_OFFERS,
    ),
    # Committed agent harness (informational): what the repo's agent config
    # runs on every session (settings and frontmatter hooks, env, apiKeyHelper,
    # .mcp.json, .cursor/hooks.json) and the settings that expose a secret or
    # widen it (tracked settings.local.json or .env, broad Bash allow, hidden
    # Unicode, credential mounts, comment-triggered deploys). Issue #513.
    ScanSpec(
        "agent_harness", scan_agent_harness,
        ("repo_root", "scope", "excludes"), STAGE_POST_OFFERS,
    ),
    # Agent working environment (Layer 5, issue #514): committed setup
    # (copilot-setup-steps.yml, devcontainer, Cursor environment, SessionStart
    # hook, setup script or target), the check command a graded instruction file
    # names, lockfiles per manifest, the Layer 5 Partial cap, and the optional
    # `gh` CI-duration probe. Reads agent_harness for the SessionStart hooks.
    # Headless runs call it with the probe off (ci_duration reports not_probed).
    ScanSpec(
        "agent_environment", scan_agent_environment,
        ("repo_root", "instruction_files", "agent_harness", "excludes"), STAGE_POST_OFFERS,
        network=True, offline_fn=partial(scan_agent_environment, probe_ci=False),
    ),
    # Shared tooling (issue #566): the shared CI sources a pipeline includes
    # (GitLab `include: project:` / `include: component:`, GitHub reusable
    # workflows) and the agent marketplaces the repository hosts or names
    # (`.claude-plugin/marketplace.json`, `extraKnownMarketplaces`), each with a
    # stable id so a fleet roll-up counts distinct sources. Offline file reads.
    ScanSpec("shared_tooling", scan_shared_tooling, ("repo_root",), STAGE_POST_OFFERS),
    # Instruction placement (Layer 0, issue #559): each instruction file split on
    # its headings, every section classified always-on or fetch-on-need, and the
    # sections to move named with line ranges and a suggested home; agent and
    # SKILL.md files over 200 lines reported separately. Advice only, no grade
    # change. Reads nested_instructions for the nested files.
    ScanSpec(
        "instruction_placement", scan_instruction_placement,
        ("repo_root", "instruction_files", "nested_instructions", "scope", "excludes"),
        STAGE_POST_OFFERS,
    ),
    # AI-assisted change share (issue #547): commits in the churn window with an
    # AI-assistant Co-authored-by trailer, as counts and a share. A lower bound
    # on agent use, never an estimate; merge commits and automation bots
    # excluded. Matcher: lib.agent_identity.
    ScanSpec(
        "ai_usage", scan_ai_usage, ("repo_root", "scope", "history_window"), STAGE_POST_OFFERS,
    ),
    # Checkable claims across the doc set (issue #491): the instruction_claims
    # kinds run over docs/ (minus the historical design records),
    # skills/*/references/ and skills/*/SKILL.md, plus the cross-document kind
    # - two files pinning different versions in one file or counting one glob
    # differently, citing every site. Layer 0 lying-signal evidence.
    ScanSpec(
        "doc_claims", scan_doc_claims,
        ("repo_root", "instruction_files", "scope", "excludes"), STAGE_POST_OFFERS,
    ),
)

validate(SCANS)
