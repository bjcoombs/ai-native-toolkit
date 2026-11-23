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

``stage`` orders entries whose key must sit somewhere other than their table
position in ``run-context.json``: ``build_run_context`` makes one
``run_scans`` call, which runs the stages in ``STAGES`` order and each stage in
table order. Every block is a table entry (#558); only ``anomalies`` uses a
stage for placement, staying the last scan key however many entries follow
it in the table.

The declared table of run-context scans and the loop that runs it. A `ScanSpec` names
the `key`, the callable, the names it `reads` (passed positionally: a core-provided
input from `PROVIDED_INPUTS` or the key of an earlier spec), its `stage`, and whether it
degrades. By default the
value is published as `ctx[key]`; a `private` spec's value (the stats, the diff, a raw
scan result) is kept in the inputs mapping for later specs and never reaches
`run-context.json`; a `spread` spec returns a mapping whose keys, all declared, are
copied into the context in returned order (the 25-key run header, the eleven keyhole
keys), with optional keys simply absent. `run_scans` routes every degrading spec
through `safe` (from `degrade.py`), which turns any exception into
`{"available": False, "reason": ...}` labelled by `label` or the key, so one broken
scan never stops the run; a degraded spread spec marks every key it declares. A spec
opts out only with a `gate_reason` drawn from `GATE_REASONS` (the setup steps, the
invalid-scope usage error, scans that normalise their own failure such as
`test_pressure` and `plugin_version`, and serialisers whose source already degrades);
every other entry degrades, and each reader of its key accepts the marker (#623:
`gap_actions` proposes no coverage action for a failed coverage read, the report and
gate say a failed `excluded_generated` was not disclosed, a failed
`mutation_not_run_cap` keeps Layer 6 capped, and `counts_only` keeps a marker under
a path-keyed or dropped key as `{"available": false}`). `validate` runs at import and raises `ScanRegistryError` on a
duplicate key, an unknown stage, a read that nothing provides, a read of a key
produced at a later stage, a callable that cannot take its declared reads, a spec
both private and spread, or a run-context key written twice, so a mis-declared scan
fails before any run. `run_scans` resolves reads outside the wrapper: an input the
core did not pass raises `ScanRegistryError` and stops the run, while a failure inside
the scan degrades. The core provides the `build_run_context` arguments
(`repo_root`, `requested_scope`, `run_date`, `non_interactive`); `scope` (the
absolute `--scope` path, or None), `instruction_files`, `excludes` (the
`(dirs, patterns)` pair from `.assess/config.toml`) and `history_window` (the stats
sidecar's churn window via `git_churn.window_from_churn_label`, or None) and
`fleet_config` (the repository's `[ai_usage]`, `[review]` and `[platform]` sections,
`lib/fleet_config.py`) are setup specs. `build_run_context` makes one `run_scans` call with no stage, which runs the
stages (`setup`, `read_side`, `post_offers`, `final`) in that order and each in table
order (#558). Only `anomalies` relies on its stage, staying the last scan key while
later entries append to the table. The `unit_readings` entry (#863) runs the collectors
the manifest marks `unit_scope` again for each monorepo unit; the module binds that pass
(`unit_readings.bind`) with a lazy table loader and `run_scans`, so the scan module never
imports the registry.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from lib import core_scans as cs
from lib import run_setup as rs
from lib.agent_building import scan_agent_building
from lib.agent_consistency import scan_agent_consistency
from lib.agent_environment import scan_agent_environment
from lib.agent_harness import scan_agent_harness
from lib.agent_ops import scan_agent_ops
from lib.ai_usage import scan_ai_usage
from lib.ci_checks import scan_ci_checks
from lib.claude_config import scan_claude_config
from lib.config_drift import scan_config_drift
from lib.degrade import safe as safe  # re-exported: callers import it from here
from lib.degrade import unavailable
from lib.coverage_gate import detect_coverage_gate
from lib.delivery import scan_delivery
from lib.external_ci import no_external_ci, read_external_ci
from lib.doc_claims import scan_doc_claims
from lib.gate_cost import estimate_gate_cost
from lib.gitlab_notes import gitlab_note_facts
from lib.instruction_claims import scan_instruction_claims
from lib.instruction_files import nested_instruction_surface
from lib.instruction_placement import scan_instruction_placement
from lib.layer_grades import scan_layer_grades
from lib.level_tally import LEVEL_READS
from lib.merged_sample import merged_sample, offline_merged_sample
from lib.gitlab_ci_lint import no_merged_ci, read_merged_ci
from lib.guardrails import scan_guardrails
from lib.review_automation import review_evidence, scan_review_automation
from lib.review_reality import review_reality_from_sample
from lib.shared_tooling import scan_shared_tooling
from lib.check_manifest import core_manifest
from lib.unit_readings import OWN_READS as UNIT_OWN_READS
from lib.unit_readings import UNIT_EXTERNAL_READS, UnitTable, unit_table
from lib.unit_readings import bind as bind_unit_pass

# Names the core passes to ``run_scans``: the arguments of
# ``build_run_context``. A spec may read these, or the key of any spec declared
# before it (``instruction_files``, ``scope``, ``excludes``, ``history_window`` and
# ``fleet_config`` are setup specs).
PROVIDED_INPUTS = frozenset({"repo_root", "requested_scope", "run_date", "non_interactive"})

STAGE_SETUP = "setup"
STAGE_READ_SIDE = "read_side"
STAGE_POST_OFFERS = "post_offers"
STAGE_FINAL = "final"
STAGES = (STAGE_SETUP, STAGE_READ_SIDE, STAGE_POST_OFFERS, STAGE_FINAL)

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


def validate(specs: Iterable[ScanSpec], provided: Iterable[str] = PROVIDED_INPUTS) -> None:
    """Raise ``ScanRegistryError`` unless every spec can run in table order.

    Stages run in ``STAGES`` order, so a read must name a provided input or a
    key whose producer is declared earlier *and* runs at the same or an earlier
    stage; a key a spread entry copies into the run context counts as produced by it,
    and reads None when that entry leaves it out. The run-context keys the table writes (published keys and spread
    keys) must be distinct.
    """
    produced_at: dict[str, int] = {name: -1 for name in provided}
    written: set[str] = set()
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
        # a spread key is readable too: it is in the run context from that stage on
        for name in spec.spread:
            produced_at[name] = stage_index


def _resolve(spec: ScanSpec, name: str, inputs: Mapping[str, Any], ctx: Mapping[str, Any],
             spread: frozenset[str] = frozenset()) -> Any:
    if name in inputs:
        return inputs[name]
    if name in ctx:
        return ctx[name]
    if name in spread:  # a spread entry may leave one of its keys out (an omitted block)
        return None
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
    stage: str | None = None,
    specs: Iterable[ScanSpec] | None = None,
) -> None:
    """Run every spec of ``stage`` in table order, writing its result.

    ``stage`` None runs every stage in ``STAGES`` order: the whole table, as
    ``build_run_context`` does.

    A published spec's value is assigned to ``ctx[spec.key]``; a private spec's
    value is stored in ``inputs[spec.key]``; a spread spec's mapping is copied
    into ``ctx`` and also stored in ``inputs[spec.key]``. A read resolves
    against ``inputs`` first, then against ``ctx``. Reads are resolved outside
    the degrade wrapper: an unresolvable read is a wiring error in the table or
    the core, so it raises ``ScanRegistryError`` and stops the run, while a
    failure inside the scan itself degrades.
    """
    table = tuple(SCANS if specs is None else specs)
    if stage is None:
        for each in STAGES:
            run_scans(ctx, inputs, each, table)
        return
    spread = frozenset(name for each in table for name in each.spread)
    for spec in table:
        if spec.stage != stage:
            continue
        args = tuple(_resolve(spec, name, inputs, ctx, spread) for name in spec.reads)
        value, degraded = _call(spec, args)
        if spec.spread:
            _spread(spec, ctx, value, degraded)
            inputs[spec.key] = value
        elif spec.private:
            inputs[spec.key] = value
        else:
            ctx[spec.key] = value


# The reasons an entry may give for opting out of the degrade wrapper (#418
# requirement 3, #623). Every other entry degrades: its readers accept the
# unavailable marker in place of its usual shape.
_SETUP = (
    "run setup: later entries and the publish step read this value, so a "
    "failure stops the run rather than feeding them an unavailable marker"
)
_BAD_SCOPE = "an invalid --scope is a usage error the CLI reports with exit code 2"
_SELF_DEGRADING = "degrades internally to an explicit unavailable result; a raise here is a bug"
_SERIALISER = "copies fields out of an earlier result that already carries its own degrade"
GATE_REASONS = frozenset({_SETUP, _BAD_SCOPE, _SELF_DEGRADING, _SERIALISER})

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
    # The fleet settings sections (#706): the repository's own `.assess/config.toml`
    # sections here; the headless signals run provides them merged with `--fleet-config`.
    ScanSpec("fleet_config", rs.fleet_config, ("repo_root",), STAGE_SETUP, private=True,
             degrade=False, gate_reason=_SELF_DEGRADING),
    # The ghsync root GitLab includes resolve against (#766, lib/ci_includes.py):
    # None here; the headless signals run provides `--ghsync-root`.
    ScanSpec("ghsync_root", rs.ghsync_root, ("repo_root",), STAGE_SETUP, private=True,
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
    # Layer 0 navigability: the doc graph.
    ScanSpec("doc_graph", cs.doc_graph, ("repo_root", "excludes", "scope", "working_notes"),
             STAGE_SETUP),
    # -- STAGE_READ_SIDE ------------------------------------------------------
    # Doc staleness: each doc's last content change against its code subject's
    # churn, following renames (#425), over the doc graph's doc->code edges.
    ScanSpec("doc_staleness", cs.doc_staleness, ("repo_root", "doc_graph", "excludes", "scope"),
             STAGE_READ_SIDE),
    ScanSpec("stale_hubs", cs.stale_hubs, ("doc_graph", "doc_staleness"), STAGE_READ_SIDE),
    # Degenerate git history (every file ~1 commit): churn-derived findings are
    # discounted. Read off the doc-staleness block, its single source of truth.
    ScanSpec("churn_degenerate", cs.churn_degenerate, ("doc_staleness",), STAGE_READ_SIDE),
    # The doc x complexity join (Signal C), read by the keyhole documentation
    # block. A failed join degrades that block, not the run.
    ScanSpec("doc_join", cs.doc_join, ("current", "doc_staleness", "repo_root"),
             STAGE_READ_SIDE, private=True, label="documentation"),
    # Instruction-surface integrity (Layer 0): uncommitted instruction files,
    # broken instruction references, and redacted sensitive content (#56).
    ScanSpec("untracked_instruction_files", cs.untracked_instruction_files,
             ("instruction_grading",), STAGE_READ_SIDE),
    ScanSpec("broken_instruction_refs", cs.instruction_refs,
             ("doc_graph", "instruction_grading"), STAGE_READ_SIDE),
    ScanSpec("sensitive_instruction_content", cs.sensitive_instruction_content,
             ("instruction_grading",), STAGE_READ_SIDE),
    # Software repo or knowledge base (#224); a knowledge base marks L2-L7 N/A.
    ScanSpec("archetype", cs.archetype, ("repo_root",), STAGE_READ_SIDE),
    # Instruction files above the repo root that reach no fresh clone (#57).
    ScanSpec("ancestor_instruction_files", cs.ancestor_instructions, ("repo_root",),
             STAGE_READ_SIDE),
    # Progressive disclosure: skills factoring and oversized instruction files.
    ScanSpec("skills", cs.skills_blocks, ("instruction_grading",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER,
             spread=("skills_present", "skills_count", "skill_files")),
    ScanSpec("instruction_file_size", cs.instruction_sizes, ("instruction_files",),
             STAGE_READ_SIDE),
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
             STAGE_READ_SIDE),
    # Layer 1 write-side truth pressure, read-only (the mutation pass is off).
    ScanSpec("test_pressure_scan", cs.test_pressure_scan,
             ("repo_root", "current", "coverage_data"), STAGE_READ_SIDE,
             private=True, label="test_pressure"),
    # normalize_test_pressure turns a failed scan into the block's own
    # unavailable shape (null mutation_config_present, empty heuristic
    # buckets), which its readers rely on; the bare marker would drop those.
    ScanSpec("test_pressure", cs.test_pressure_block, ("test_pressure_scan",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SELF_DEGRADING),
    # Layer 6 cap while the mutation tier has not run; assess_finalize enforces it.
    ScanSpec("mutation_not_run_cap", cs.mutation_cap, ("test_pressure",), STAGE_READ_SIDE),
    ScanSpec("test_focus", cs.test_focus,
             ("repo_root", "current", "coverage_data", "test_pressure", "hot_test_index"),
             STAGE_READ_SIDE),
    ScanSpec("promissory_markers", cs.promissory_block, ("promissory",), STAGE_READ_SIDE),
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
    # The shared sample of merged changes (#715): up to 30 merged in the run
    # window, newest merge first. Private; review_reality and delivery read it,
    # so one listing serves both and they describe the same changes. Offline it reads
    # the remote only: no_remote with none, else network (#908).
    ScanSpec("merged_sample", merged_sample, ("repo_root", "history_window", "run_date"),
             STAGE_READ_SIDE, private=True, network=True, offline_fn=offline_merged_sample),
    # Every sampled GitLab merge request's notes, read once, kept as facts (#716).
    ScanSpec("gitlab_note_facts", gitlab_note_facts, ("merged_sample", "fleet_config"),
             STAGE_READ_SIDE, private=True, network=True),
    # Offline the sample is a reason, which the block passes through (#908).
    ScanSpec("review_reality", review_reality_from_sample, ("merged_sample", "gitlab_note_facts"),
             STAGE_READ_SIDE, network=True, offline_fn=review_reality_from_sample),
    # Delivery figures (#715): time to merge, first human comment, AI review;
    # review_reality's approved and self-merged shares are copied, not recomputed.
    ScanSpec("delivery", scan_delivery,
             ("merged_sample", "review_reality", "fleet_config", "gitlab_note_facts"),
             STAGE_READ_SIDE, network=True),
    ScanSpec("gate_cost_estimate", estimate_gate_cost, ("repo_root",), STAGE_READ_SIDE, network=True),
    # Layer 6: is a coverage threshold *configured*, not just measured? Each gate
    # names its file, line and threshold as written; none found reads
    # configured: false, never inferred from coverage_report. A scoped run reads
    # the scope plus the root CI and task-runner files (issue #488).
    ScanSpec(
        "coverage_gate", detect_coverage_gate,
        ("repo_root", "scope", "excludes"), STAGE_READ_SIDE,
    ),
    # Accretion filtered to files already in the top complexity/size band.
    ScanSpec("accretion_ratchet", cs.accretion_ratchet, ("accretion_scan", "current"),
             STAGE_READ_SIDE),
    # Keyhole readiness: structure, behaviour, documentation, understanding and
    # runtime blocks, derived findings, attention and the report products.
    # integrate() wraps each block in its own catch-all.
    ScanSpec(
        "keyhole", cs.keyhole,
        ("repo_root", "current", "doc_staleness", "liveness_blocks", "structure_scan",
         "test_pressure", "promissory", "accretion_ratchet", "archetype", "excludes", "scope",
         "rename_map", "doc_join"),
        STAGE_READ_SIDE, private=True, degrade=False, gate_reason=_SELF_DEGRADING,
    ),
    ScanSpec("keyhole_blocks", cs.keyhole_blocks, ("keyhole",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER, spread=KEYHOLE_KEYS),
    ScanSpec("exclusion_disclosures", cs.exclusion_disclosures, ("keyhole", "excludes"),
             STAGE_READ_SIDE, degrade=False, gate_reason=_SERIALISER,
             spread=("excluded_by_config", "excluded_as_archive", "pruned_finding_paths")),
    ScanSpec("excluded_generated", cs.generated_exclusions, ("current",), STAGE_READ_SIDE),
    # Declared ownership map vs where the code lives; omitted with no map.
    ScanSpec("structure_drift_block", cs.structure_drift, ("repo_root", "keyhole"),
             STAGE_READ_SIDE, degrade=False, gate_reason=_SELF_DEGRADING,
             spread=("structure_drift",)),
    # "unknown" when the manifest cannot be read: a string, as decline reads it.
    ScanSpec("plugin_version", rs.read_plugin_version, (), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SELF_DEGRADING),
    # Active permanent declines of the optional tools (.assess/.no-<tool>).
    ScanSpec("decline", cs.decline_blocks, ("assess_dir", "plugin_version"), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER,
             spread=("decline_markers", "reoffer_mutation", "decline_disclosures")),
    # The owner's condition exclusions (.assess/config.toml [checks], #910); the level reads them.
    ScanSpec("owner_exclusions", cs.owner_exclusions, ("repo_root",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SELF_DEGRADING),
    # Monorepo units (#862): the shallowest build-manifest folders, a workspace root's
    # declared members in its place, or the [[units]] of .assess/config.toml. Core-only.
    ScanSpec("units", cs.units, ("repo_root", "excludes"), STAGE_READ_SIDE),
    # Headless runs pre-record every offer as skipped; never inferred from stdin.
    ScanSpec("offers_block", cs.offers_blocks, ("non_interactive",), STAGE_READ_SIDE,
             degrade=False, gate_reason=_SERIALISER, spread=("interactive", "offers")),
    ScanSpec("uninstall_instructions_path", cs.uninstall_instructions_path, (), STAGE_READ_SIDE),
    # -- STAGE_FINAL: last key of run-context.json ----------------------------
    ScanSpec("anomalies", cs.anomalies, ("run_header",), STAGE_FINAL),
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
    # AI review committed in CI (issue #696): a GitHub workflow step running a
    # pinned review action, a GitLab review component or include (pinned, or
    # listed in [review] ai_review_components), or an AI review app's config
    # file. Offline file reads; review_evidence (Layer 7 level 1) reads it with
    # review_reality and is rebuilt by the --platform pass (PLATFORM_SCANS).
    # GitLab's merged CI configuration (#796, lib/gitlab_ci_lint.py): the CI lint API,
    # asked only where an include did not resolve offline. Private, held in memory;
    # review_automation and ci_checks read it. Offline it reads nothing.
    ScanSpec("gitlab_merged_ci", read_merged_ci, ("repo_root", "ghsync_root"), STAGE_POST_OFFERS,
             private=True, network=True, offline_fn=no_merged_ci),
    ScanSpec("review_automation", scan_review_automation,
             ("repo_root", "fleet_config", "ghsync_root", "gitlab_merged_ci"),
             STAGE_POST_OFFERS),
    ScanSpec("review_evidence", review_evidence, ("review_automation", "review_reality"),
             STAGE_POST_OFFERS),
    # CI checks (issue #698): whether a GitHub Actions or GitLab CI line runs a
    # test or lint command (check_command.is_check_command); null when no
    # check is found but a CI file pulls jobs this run cannot read (an unresolved
    # include:, a remote reusable workflow; GitLab includes resolve offline against
    # `ghsync_root`, #766; under --platform, GitLab's merged configuration where one did
    # not, #796). enforced_check_runs (#775) leaves out masked
    # checks and checks off the ordinary triggers (lib/ci_masking.py,
    # lib/ci_triggers.py); Layer 5 level 1 of the default maturity model reads it.
    # lint_runs (#782) is the same test for lint lines; Layer 3 level 1 reads it.
    # Offline itself; under --platform it also reads external_ci (#928, lib/external_ci.py):
    # the check runs and statuses of the sampled merged pull requests, at most two GETs each.
    # Private, two counts in memory; offline it reads nothing.
    ScanSpec("external_ci", read_external_ci, ("merged_sample",), STAGE_POST_OFFERS,
             private=True, network=True, offline_fn=no_external_ci),
    ScanSpec("ci_checks", scan_ci_checks,
             ("repo_root", "ghsync_root", "gitlab_merged_ci", "external_ci"), STAGE_POST_OFFERS),
    # Agent building (issue #689): what the repository builds with agents, an
    # inventory and not scored - agent SDKs and frameworks in manifests, agent
    # configuration, a counts-only tool-call loop / MCP server heuristic and the
    # issue-tracker plus Git-host API-client pairing, reduced to one class. The
    # fleet roll-up counts classes per group and clusters similar builders.
    ScanSpec("agent_building", scan_agent_building, ("repo_root",), STAGE_POST_OFFERS),
    # Deterministic layer grades (issue #620): instructions_grade republished as
    # a block, so a maturity model can name it in a headless or full run, with
    # the three-way instruction presence (root, nested_only, none; issue #821)
    # read from the root grading and the nested block.
    ScanSpec("layer_grades", scan_layer_grades, ("instruction_grading", "nested_instructions"),
             STAGE_POST_OFFERS),
    # Top 3 candidates read from measured gaps. Post-offers since #821: the
    # root-instructions gap reads layer_grades.
    ScanSpec("gap_actions", cs.gap_actions,
             ("coverage_report", "doc_graph", "current", "archetype", "layer_grades",
              "nested_instructions"),
             STAGE_POST_OFFERS),
    # Instruction placement (Layer 0, issue #559): each instruction file split on
    # its headings, every section classified always-on or fetch-on-need, and the
    # sections to move named with line ranges and a suggested home; agent and
    # SKILL.md files over 200 lines reported separately. Advice; the grader
    # scores instruction files on the same split (#560). Reads
    # nested_instructions for the nested files.
    ScanSpec(
        "instruction_placement", scan_instruction_placement,
        ("repo_root", "instruction_files", "nested_instructions", "scope", "excludes"),
        STAGE_POST_OFFERS,
    ),
    # Cross-agent instruction consistency (issue #768): whether the root
    # instruction files of every agent system present (Claude Code, AGENTS.md,
    # Copilot, Cursor, Windsurf, Cline, Aider, Junie, Amazon Q, Kiro, Continue,
    # Zed; lib/agent_systems.py) name the same check commands and thresholds,
    # and whether each links to one canonical file. Feeds the agent-consistency row.
    ScanSpec("agent_consistency", scan_agent_consistency, ("repo_root", "excludes"),
             STAGE_POST_OFFERS),
    # AI-assisted change share (issue #547): commits in the churn window with an
    # AI-assistant Co-authored-by trailer, as counts and a share. A lower bound
    # on agent use, never an estimate; merge commits and automation bots
    # excluded. Since #695 also the commits an agent identity authored (pinned
    # defaults plus the [ai_usage] rules of `fleet_config`). Matcher: lib.agent_identity.
    ScanSpec(
        "ai_usage", scan_ai_usage, ("repo_root", "scope", "history_window", "fleet_config"),
        STAGE_POST_OFFERS,
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
    # Guardrails stated (issue #767): the gates the repository configures (ruff or
    # flake8 complexity limit, the coverage_gate floor, pre-commit hooks, mypy
    # strict), each checked against the graded instruction files; an unstated gate
    # carries a ready one-line snippet. stated_pct feeds the guardrails-stated row.
    ScanSpec(
        "guardrails", scan_guardrails,
        ("repo_root", "instruction_files", "coverage_gate", "excludes"), STAGE_POST_OFFERS,
    ),
)


# The verified tally and the autonomy Step (lib/level_tally.py, #744): the
# default maturity model over the finished run context. Core-only: the
# headless runner never reports it; /fleet computes the same from each row.
SCANS = (*SCANS, ScanSpec("level", cs.level, LEVEL_READS, STAGE_POST_OFFERS))
# The deterministic headline (lib/headline.py, #747): the Step, its next unlock and
# "Verified practices: N of M measured", worded from the level block. The report
# pastes it verbatim and the badge and log entry carry its summary.
SCANS = (*SCANS, ScanSpec("headline", cs.headline, ("level",), STAGE_POST_OFFERS))
# Monorepo unit readings (lib/unit_readings.py, #863): the scoped and override scans again
# for each unit of the units block, every condition tagged unit or inherited. Core-only.
SCANS = (*SCANS, ScanSpec("unit_readings", cs.unit_readings,
                          (*UNIT_OWN_READS, *UNIT_EXTERNAL_READS), STAGE_POST_OFFERS))

validate(SCANS)

# The unit pass (lib/unit_readings.py, #863), bound here so that scan module never imports
# the registry. The table is built on first use: the headless variants it reads import
# this module, so they cannot be imported while it loads.
def _unit_table() -> UnitTable:
    from lib.headless_layers import HEADLESS_SCANS as layer_scans
    from lib.headless_static import HEADLESS_SCANS as static_scans
    return unit_table(core_manifest(), SCANS, (*layer_scans, *static_scans))


bind_unit_pass(_unit_table, run_scans)
