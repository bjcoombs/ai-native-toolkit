"""Declared table of run-context scans, and the loop that runs them.

``assess_core.build_run_context`` grew by one import and one assignment per
scan until it was the most complex function in the repository, and the "a
broken scan must never block the assessment" rule held only where the author
remembered to wrap the call. Here a scan is declared once - its run-context
key, its callable, what it reads, how it degrades - and one loop runs the
table, so the degrade wrapper applies by construction and adding a scan does
not edit ``build_run_context``. The shape follows ``_DEAD_CODE_TOOLS`` in
``liveness_scan``: spec entries plus a single driving loop.

Each entry names its key and callable (plus any spread keys and offline callable) in code;
``spec_from_manifest`` reads the rest - ``reads``, ``stage``, ``degrade``, ``gate_reason``,
``label``, ``private``, ``network`` - from the key's collector entry in
``checks/core/<key>.toml`` (#953), so a scan's inputs change with no Python edit.

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
(`repo_root`, `requested_scope`, `run_date`, `non_interactive`, and `maturity_model`: the
`--profile` or `--levels` choice, `lib.profiles.ModelChoice`, or None for the default model,
which the `level` and `headline` entries read, #1150; and `packs`, the `--pack` files loaded by
`lib.packs.load_pack` in pass order, empty without the flag, which the `pack_checks` entry
reads, #1273); `scope` (the
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
imports the registry. A `--profile` or `--levels` run passes `run_scans` a `plan`
(`lib/scan_plan.py`, #1268): only its scans run, and each skipped entry leaves the plan's
"not run under <model>" marker where its value would go; the `scan_plan_stamp` entry
publishes the plan as `scan_plan`, and nothing for a full run.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from functools import partial
from typing import Any

import lib.manifest.check_manifest as cm
from lib import core_scans as cs
from lib import run_setup as rs
from lib.agents.agent_building import scan_agent_building
from lib.agent_consistency import scan_agent_consistency
from lib.agents.agent_environment import scan_agent_environment
from lib.agents.agent_harness import scan_agent_harness
from lib.agents.agent_ops import scan_agent_ops
from lib.agent_outcome import scan_agent_outcome
from lib.ai_usage import scan_ai_usage
from lib.ci_checks import scan_ci_checks
from lib.claude_config import scan_claude_config
from lib.hosting.config_drift import scan_config_drift
from lib.degrade import safe as safe  # re-exported: callers import it from here
from lib.degrade import unavailable
from lib.testing.coverage_gate import detect_coverage_gate
from lib.hosting.delivery import scan_delivery
from lib.external_ci import no_external_ci, read_external_ci
from lib.docs.doc_claims import scan_doc_claims
from lib.gate_cost import estimate_gate_cost
from lib.hosting.gitlab_notes import gitlab_note_facts
from lib.instruction_claims import scan_instruction_claims
from lib.instruction_files import nested_instruction_surface
from lib.instruction_placement import scan_instruction_placement
from lib.skill_disclosure import scan_skill_disclosure
from lib.secrets_guard import scan_secrets_guard
from lib.skill_script_tests import scan_skill_script_tests
from lib.skill_personal_data import scan_skill_personal_data
from lib.ci_model_jobs import scan_ci_model_jobs
from lib.pack_checks import IDENTITY_BLOCK, PACK_BLOCK
from lib.pack_checks import collect as collect_packs
from lib.hosting.org_security_policy import scan_org_security_policy
from lib.hosting.github_secret_scanning import scan_github_secret_scanning
from lib.layer_grades import scan_layer_grades
from lib.hosting.merged_sample import merged_sample, offline_merged_sample
from lib.hosting.gitlab_ci_lint import no_merged_ci, read_merged_ci
from lib.guardrails import scan_guardrails
from lib.review_automation import review_evidence, scan_review_automation
from lib.hosting.review_reality import review_reality_from_sample
from lib.shared_tooling import scan_shared_tooling
from lib.manifest.check_manifest import core_manifest
from lib.scan_plan import ScanPlan
from lib.unit_levels import READS as UNIT_LEVEL_READS
from lib.unit_readings import OWN_READS as UNIT_OWN_READS
from lib.unit_readings import UNIT_EXTERNAL_READS, UnitTable, unit_table
from lib.unit_readings import bind as bind_unit_pass

# Names the core passes to ``run_scans``: the arguments of
# ``build_run_context``. A spec may read these, or the key of any spec declared
# before it (``instruction_files``, ``scope``, ``excludes``, ``history_window`` and
# ``fleet_config`` are setup specs). ``plan`` is the run's ``lib.scan_plan.ScanPlan``, or
# None for a full run (#1268).
PROVIDED_INPUTS = frozenset({"repo_root", "requested_scope", "run_date", "non_interactive",
                             "maturity_model", "managed_settings", "plan", "packs"})

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
    plan: ScanPlan | None = None,
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

    ``plan`` (``lib/scan_plan.py``, #1268; by default the ``plan`` input) runs only ``plan.scans``: a skipped
    entry's value is ``plan.marker()``, written where its value would have gone
    (each spread key, the inputs for a private entry), and it is never called.
    """
    table = tuple(SCANS if specs is None else specs)
    # ``plan`` is optional for callers that predate it (#1268): absent means every scan runs
    plan = inputs.setdefault("plan", None) if plan is None else plan
    if stage is None:
        for each in STAGES:
            run_scans(ctx, inputs, each, table, plan)
        return
    spread = frozenset(name for each in table for name in each.spread)
    for spec in table:
        if spec.stage != stage:
            continue
        if plan is not None and not plan.runs(spec.key):
            value, degraded = plan.marker(), True
        else:
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
# The manifest's ``gate_reason`` token -> the reason text (``check_manifest.GATE_REASONS``).
GATE_REASON_TEXT = {"setup": _SETUP, "bad_scope": _BAD_SCOPE,
                    "self_degrading": _SELF_DEGRADING, "serialiser": _SERIALISER}


def spec_from_manifest(manifest: cm.CheckManifest, key: str, fn: Callable[..., Any], *,
                       spread: tuple[str, ...] = (),
                       offline_fn: Callable[..., Any] | None = None) -> ScanSpec:
    """The ``ScanSpec`` for ``key``, its metadata read from the key's collector entry (#953).

    ``reads``, ``stage``, ``degrade``, ``gate_reason``, ``label``, ``private`` and
    ``network`` come from the manifest; the callables, the spread keys and the table order
    stay in code. A key with no collector entry, or a spread that disagrees with the
    entry's ``spread`` flag, raises ``ScanRegistryError``.
    """
    entry = next((c for c in cm.collectors(manifest) if cm.scan_key(c) == key), None)
    if entry is None:
        raise ScanRegistryError(f"scan {key!r} has no collector entry in the check manifest")
    if bool(spread) != entry.get("spread", False):
        raise ScanRegistryError(f"scan {key!r}: spread keys disagree with the manifest's spread flag")
    gate = entry.get("gate_reason")
    return ScanSpec(key, fn, tuple(entry["reads"]), entry["stage"],
                    degrade=entry.get("degrade", True),
                    gate_reason="" if gate is None else GATE_REASON_TEXT[gate],
                    label=entry.get("label", ""), private=entry.get("private", False),
                    spread=spread, network=entry.get("network", False), offline_fn=offline_fn)


def pinned_reads(spec: ScanSpec, reads: tuple[str, ...]) -> ScanSpec:
    """``spec`` unchanged when its manifest ``reads`` are exactly ``reads``, in order; else
    ``ScanRegistryError``. For a callable that zips its arguments with a code tuple."""
    if spec.reads != reads:
        raise ScanRegistryError(f"scan {spec.key!r}: the manifest reads {list(spec.reads)}, "
                                f"but its callable zips them with {list(reads)}")
    return spec


def _spec(key: str, fn: Callable[..., Any], *, spread: tuple[str, ...] = (),
          offline_fn: Callable[..., Any] | None = None) -> ScanSpec:
    """``spec_from_manifest`` over the core manifest: one table entry."""
    return spec_from_manifest(cm.core_manifest(), key, fn, spread=spread, offline_fn=offline_fn)

KEYHOLE_KEYS = (
    "structure", "behaviour", "documentation", "understanding", "runtime",
    "derived_findings", "attention", "attention_low_signal", "findings_markdown",
    "keyhole_summary", "prescribed_actions",
)


SCANS: tuple[ScanSpec, ...] = (
    # -- STAGE_SETUP: the run's own state, then the run header ---------------
    # Every value a later entry or the publish step needs: a failure here
    # stops the run, as it did before these steps moved into the table.
    _spec("run_scope", rs.run_scope),
    _spec("scope", rs.scope_root),
    _spec("assess_dir", rs.run_assess_dir),
    # Minted once and stamped on every artifact of this run, so finalize can
    # prove the finalize-input it later consumes matches this run-context.
    _spec("run_id", rs.new_run_id),
    _spec("current", rs.current_stats),
    _spec("prior", rs.prior_stats),
    _spec("excluded_as_generated", rs.generated_paths),
    _spec("diff", rs.stats_diff),
    _spec("instruction_grading", rs.instruction_grading),
    _spec("instruction_files", rs.graded_instruction_files),
    # Historical path -> current path from git's rename detection, built once:
    # it re-keys the first-flagged map and folds co-change history in keyhole.
    _spec("rename_map", rs.rename_map),
    _spec("first_flagged", rs.first_flagged),
    # `.assess/config.toml` excludes, loaded once and read by every scan.
    _spec("excludes", rs.config_excludes),
    # The git-history window a history-reading scan honours: the stats
    # sidecar's churn window in the `<n>d|w|mo|y` grammar, or None for all
    # history. The headless signals run provides `--window` instead.
    _spec("history_window", rs.history_window),
    # The fleet settings sections (#706): the repository's own `.assess/config.toml`
    # sections here; the headless signals run provides them merged with `--fleet-config`.
    _spec("fleet_config", rs.fleet_config),
    # The ghsync root GitLab includes resolve against (#766, lib/ci_includes.py):
    # None here; the headless signals run provides `--ghsync-root`.
    _spec("ghsync_root", rs.ghsync_root),
    _spec("measured_commit", rs.measured_commit),
    _spec("superseded", rs.superseded_run),
    # Files first flagged only by a never-finalized run and now excluded (#356).
    _spec("unfinalized", rs.unfinalized_exclusions),
    # Promissory markers, scanned here because the hotspot pages carry each
    # file's marker debt; published later as promissory_markers.
    _spec("promissory", cs.promissory_scan),
    # Accretion ratchet over every file: never raises (it degrades to
    # available=False itself) and returns a dataclass, not a dict.
    _spec("accretion_scan", cs.accretion_scan),
    _spec("accretion_by_file", cs.accretion_by_file),
    # One repository test index, shared by test_focus and the hotspot pages.
    _spec("hot_test_index", cs.hot_test_index),
    # The first 25 run-context keys; the four wiki keys are filled after the
    # wiki is written (lib/run_publish.py).
    _spec("run_header", rs.run_header, spread=rs.RUN_HEADER_KEYS),
    # The scan plan a --profile or --levels run ran under (lib/scan_plan.py, #1268): the
    # scans it ran and left out. Absent from a full run, whose run context is unchanged.
    _spec("scan_plan_stamp", rs.scan_plan_stamp, spread=("scan_plan",)),
    _spec("working_notes", cs.working_notes),
    # Layer 0 navigability: the doc graph.
    _spec("doc_graph", cs.doc_graph),
    # -- STAGE_READ_SIDE ------------------------------------------------------
    # Doc staleness: each doc's last content change against its code subject's
    # churn, following renames (#425), over the doc graph's doc->code edges.
    _spec("doc_staleness", cs.doc_staleness),
    _spec("stale_hubs", cs.stale_hubs),
    # Degenerate git history (every file ~1 commit): churn-derived findings are
    # discounted. Read off the doc-staleness block, its single source of truth.
    _spec("churn_degenerate", cs.churn_degenerate),
    # The doc x complexity join (Signal C), read by the keyhole documentation
    # block. A failed join degrades that block, not the run.
    _spec("doc_join", cs.doc_join),
    # Instruction-surface integrity (Layer 0): uncommitted instruction files,
    # broken instruction references, and redacted sensitive content (#56).
    _spec("untracked_instruction_files", cs.untracked_instruction_files),
    _spec("broken_instruction_refs", cs.instruction_refs),
    _spec("sensitive_instruction_content", cs.sensitive_instruction_content),
    # Software repo or knowledge base (#224); a knowledge base marks L2-L7 N/A.
    _spec("archetype", cs.archetype),
    # Instruction files above the repo root that reach no fresh clone (#57).
    _spec("ancestor_instruction_files", cs.ancestor_instructions),
    # Progressive disclosure: skills factoring and oversized instruction files.
    _spec("skills", cs.skills_blocks, spread=("skills_present", "skills_count", "skill_files")),
    _spec("instruction_file_size", cs.instruction_sizes),
    # Layer 1 liveness. A failed scan yields explicit unavailable blocks, never
    # "no observability"; capability keys appear only when a language is found.
    _spec("liveness", cs.liveness_scan),
    _spec("liveness_blocks", cs.liveness_blocks,
          spread=("dead_code", "observability", "capability_offers", "language_capabilities")),
    _spec("structure_scan", cs.structure_scan),
    # Line coverage from a report the project already generated; /assess never
    # runs the suite. None when absent or malformed.
    _spec("coverage_data", cs.coverage_data),
    _spec("coverage_report", cs.coverage_report),
    # Layer 1 write-side truth pressure, read-only (the mutation pass is off).
    _spec("test_pressure_scan", cs.test_pressure_scan),
    # normalize_test_pressure turns a failed scan into the block's own
    # unavailable shape (null mutation_config_present, empty heuristic
    # buckets), which its readers rely on; the bare marker would drop those.
    _spec("test_pressure", cs.test_pressure_block),
    # Layer 6 cap while the mutation tier has not run; assess_finalize enforces it.
    _spec("mutation_not_run_cap", cs.mutation_cap),
    _spec("test_focus", cs.test_focus),
    _spec("promissory_markers", cs.promissory_block),
    # Agent-operations guardrails (permission allowlists, hooks, sandbox rules,
    # routine definitions): Layer 8 workflow-maturity evidence. Tracked-only
    # credit - an uncommitted settings file reaches no clone. Deliberately
    # excludes .claude/agents/ and .claude/skills/ (Layer 0's evidence) so the
    # two layers never double-count the same artifact.
    _spec("agent_ops", scan_agent_ops),
    # Configuration drift (Layer 5 lying signal): tracked ruleset and
    # branch-protection snapshots diffed against the live GitHub setting via
    # `gh`. Optional: no remote, no `gh`, no auth or a refused read degrades to
    # available: false with the reason, never a clean result.
    _spec("config_drift", scan_config_drift),
    # The shared sample of merged changes (#715): up to 30 merged in the run
    # window, newest merge first. Private; review_reality and delivery read it,
    # so one listing serves both and they describe the same changes. Offline it reads
    # the remote only: no_remote with none, else network (#908).
    _spec("merged_sample", merged_sample, offline_fn=offline_merged_sample),
    # Every sampled GitLab merge request's notes, read once, kept as facts (#716).
    _spec("gitlab_note_facts", gitlab_note_facts),
    # Offline the sample is a reason, which the block passes through (#908).
    _spec("review_reality", review_reality_from_sample, offline_fn=review_reality_from_sample),
    # Delivery figures (#715): time to merge, first human comment, AI review;
    # review_reality's approved and self-merged shares are copied, not recomputed.
    _spec("delivery", scan_delivery),
    _spec("gate_cost_estimate", estimate_gate_cost),
    # Layer 6: is a coverage threshold *configured*, not just measured? Each gate
    # names its file, line and threshold as written; none found reads
    # configured: false, never inferred from coverage_report. A scoped run reads
    # the scope plus the root CI and task-runner files (issue #488).
    _spec("coverage_gate", detect_coverage_gate),
    # Accretion filtered to files already in the top complexity/size band.
    _spec("accretion_ratchet", cs.accretion_ratchet),
    # Keyhole readiness: structure, behaviour, documentation, understanding and
    # runtime blocks, derived findings, attention and the report products.
    # integrate() wraps each block in its own catch-all.
    _spec("keyhole", cs.keyhole),
    _spec("keyhole_blocks", cs.keyhole_blocks, spread=KEYHOLE_KEYS),
    _spec("exclusion_disclosures", cs.exclusion_disclosures,
          spread=("excluded_by_config", "excluded_as_archive", "pruned_finding_paths")),
    _spec("excluded_generated", cs.generated_exclusions),
    # Declared ownership map vs where the code lives; omitted with no map.
    _spec("structure_drift_block", cs.structure_drift, spread=("structure_drift",)),
    # "unknown" when the manifest cannot be read: a string, as decline reads it.
    _spec("plugin_version", rs.read_plugin_version),
    # Active permanent declines of the optional tools (.assess/.no-<tool>).
    _spec("decline", cs.decline_blocks,
          spread=("decline_markers", "reoffer_mutation", "decline_disclosures")),
    # The owner's condition exclusions (.assess/config.toml [checks], #910); the level reads them.
    _spec("owner_exclusions", cs.owner_exclusions),
    # Monorepo units (#862): the shallowest build-manifest folders, a workspace root's
    # declared members in its place, or the [[units]] of .assess/config.toml. The headless
    # runner reports it under --units (signals.UNIT_SCANS, #866).
    _spec("units", cs.units),
    # Headless runs pre-record every offer as skipped; never inferred from stdin.
    _spec("offers_block", cs.offers_blocks, spread=("interactive", "offers")),
    _spec("uninstall_instructions_path", cs.uninstall_instructions_path),
    # -- STAGE_FINAL: last key of run-context.json ----------------------------
    _spec("anomalies", cs.anomalies),
    # -- STAGE_POST_OFFERS ----------------------------------------------------
    # Checkable claims in the graded instruction files ("`x.sh` is enforced in
    # CI", "Node 20.11.0 is pinned in `.nvmrc`"), verified against the repo; a
    # failed claim is a Layer 0 lying signal. Zeros when none.
    _spec("instruction_claims", scan_instruction_claims),
    # Claude Code configuration (Layer 0): legacy command files and skill,
    # command and agent frontmatter the runtime silently ignores, checked
    # against the documented field sets pinned in lib/claude_config_fields.py.
    # available: false when the run root has no .claude/ and no plugin
    # manifest. Last in the table so every earlier key keeps its position.
    _spec("claude_config", scan_claude_config),
    # Nested and path-scoped instruction files (Layer 0, issue #511): nested
    # CLAUDE.md / AGENTS.md / GEMINI.md, .claude/rules, Copilot and Cursor
    # rules, each graded with its scope; the always-loaded budget per tool;
    # and dead scope globs, ignored .cursor .md files, a CLAUDE.md shadowing a
    # different AGENTS.md, and nested files that repeat their parent.
    _spec("nested_instructions", nested_instruction_surface),
    # Committed agent harness (informational): what the repo's agent config
    # runs on every session (settings and frontmatter hooks, env, apiKeyHelper,
    # .mcp.json, .cursor/hooks.json) and the settings that expose a secret or
    # widen it (tracked settings.local.json or .env, broad Bash allow, hidden
    # Unicode, credential mounts, comment-triggered deploys). Issue #513.
    _spec("agent_harness", scan_agent_harness),
    # Agent working environment (Layer 5, issue #514): committed setup
    # (copilot-setup-steps.yml, devcontainer, Cursor environment, SessionStart
    # hook, setup script or target), the check command a graded instruction file
    # names, lockfiles per manifest, the Layer 5 Partial cap, and the optional
    # `gh` CI-duration probe. Reads agent_harness for the SessionStart hooks.
    # Headless runs call it with the probe off (ci_duration reports not_probed).
    _spec("agent_environment", scan_agent_environment,
          offline_fn=partial(scan_agent_environment, probe_ci=False)),
    # Shared tooling (issue #566): the shared CI sources a pipeline includes
    # (GitLab `include: project:` / `include: component:`, GitHub reusable
    # workflows) and the agent marketplaces the repository hosts or names
    # (`.claude-plugin/marketplace.json`, `extraKnownMarketplaces`), each with a
    # stable id so a fleet roll-up counts distinct sources. Offline file reads.
    _spec("shared_tooling", scan_shared_tooling),
    # AI review committed in CI (issue #696): a GitHub workflow step running a
    # pinned review action, a GitLab review component or include (pinned, or
    # listed in [review] ai_review_components), or an AI review app's config
    # file. Offline file reads; review_evidence (Layer 7 level 1) reads it with
    # review_reality and is rebuilt by the --platform pass (PLATFORM_SCANS).
    # GitLab's merged CI configuration (#796, lib/hosting/gitlab_ci_lint.py): the CI lint API,
    # asked only where an include did not resolve offline. Private, held in memory;
    # review_automation and ci_checks read it. Offline it reads nothing.
    _spec("gitlab_merged_ci", read_merged_ci, offline_fn=no_merged_ci),
    _spec("review_automation", scan_review_automation),
    _spec("review_evidence", review_evidence),
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
    _spec("external_ci", read_external_ci, offline_fn=no_external_ci),
    _spec("ci_checks", scan_ci_checks),
    # Agent building (issue #689): what the repository builds with agents, an
    # inventory and not scored - agent SDKs and frameworks in manifests, agent
    # configuration, a counts-only tool-call loop / MCP server heuristic and the
    # issue-tracker plus Git-host API-client pairing, reduced to one class. The
    # fleet roll-up counts classes per group and clusters similar builders.
    _spec("agent_building", scan_agent_building),
    # Deterministic layer grades (issue #620): instructions_grade republished as
    # a block, so a maturity model can name it in a headless or full run, with
    # the three-way instruction presence (root, nested_only, none; issue #821)
    # read from the root grading and the nested block.
    _spec("layer_grades", scan_layer_grades),
    # Instruction placement (Layer 0, issue #559): each instruction file split on
    # its headings, every section classified always-on or fetch-on-need, and the
    # sections to move named with line ranges and a suggested home; agent and
    # SKILL.md files over 200 lines reported separately. Advice; the grader
    # scores instruction files on the same split (#560). Reads
    # nested_instructions for the nested files.
    _spec("instruction_placement", scan_instruction_placement),
    # Skill disclosure (issue #1045): each SKILL.md and agent definition against Anthropic's
    # skill authoring guidance (body lines, tokens, name and description, references one level
    # deep). Feeds the skill-disclosure row; scoped per skill unit (unit_kinds = ["skill"]).
    _spec("skill_disclosure", scan_skill_disclosure),
    # Skill script tests (issue #1045): whether each skill's scripts have tests beside them in
    # its own folder (presence, not coverage). Scoped per skill unit (unit_kinds = ["skill"]).
    _spec("skill_script_tests", scan_skill_script_tests),
    # Personal data in skill text (issue #1045): the kinds found in each skill and agent file and
    # its skill's references/, never the data. Scoped per skill unit (unit_kinds = ["skill"]).
    _spec("skill_personal_data", scan_skill_personal_data),
    # Model-backed CI jobs (issue #1242): each job that calls a model API (an eval harness, an
    # agent action or CLI, a model-provider secret) and whether a gate limits it. Feeds the
    # model-ci-gated row.
    _spec("ci_model_jobs", scan_ci_model_jobs),
    # Contributor files (issue #1045): a contributing guide and a security policy (or a link to
    # GitHub private vulnerability reporting), with issue and change templates as evidence.
    # Feeds the contributor-files row; not measured for a knowledge base (reads archetype).
    # The owner's shared security policy (issue #1045): the SECURITY file of the owner's
    # public .github repository, which GitHub shows on a repository that has none. Network.
    _spec("org_security_policy", scan_org_security_policy),
    # GitHub's own secret scanning and push protection settings (issue #1173), admin-only. Network.
    _spec("github_secret_scanning", scan_github_secret_scanning),
    _spec("contributor_files", cs.contributor_files),
    # Secrets guard (issue #1173): a secrets hook in the pre-commit config and a secrets scan in CI,
    # each with its own grade; level 1 any guard (listed), level 2 one an agent cannot skip (counted).
    _spec("secrets_guard", scan_secrets_guard),
    # Top 3 candidates read from measured gaps. Post-offers since #821: the
    # root-instructions gap reads layer_grades; the removal proposal (#1084)
    # reads instruction_placement, so it runs after it.
    _spec("gap_actions", cs.gap_actions),
    # Cross-agent instruction consistency (issue #768): whether the root
    # instruction files of every agent system present (Claude Code, AGENTS.md,
    # Copilot, Cursor, Windsurf, Cline, Aider, Junie, Amazon Q, Kiro, Continue,
    # Zed; lib/agent_systems.py) name the same check commands and thresholds,
    # and whether each links to one canonical file. Feeds the agent-consistency row.
    _spec("agent_consistency", scan_agent_consistency),
    # AI-assisted change share (issue #547): commits in the churn window with an
    # AI-assistant Co-authored-by trailer, as counts and a share. A lower bound
    # on agent use, never an estimate; merge commits and automation bots
    # excluded. Since #695 also the commits an agent identity authored (pinned
    # defaults plus the [ai_usage] rules of `fleet_config`). Matcher: lib.agent_identity.
    _spec("ai_usage", scan_ai_usage),
    # Agent commit outcomes (issue #1085): of the ai_usage agent commits (trailer or
    # agent author), the share later reverted and the share whose added lines were
    # rewritten within REWORK_DAYS. Informational: no Step, count or headline reads it.
    _spec("agent_outcome", scan_agent_outcome),
    # Checkable claims across the doc set (issue #491): the instruction_claims
    # kinds run over docs/ (minus the historical design records),
    # skills/*/references/ and skills/*/SKILL.md, plus the cross-document kind
    # - two files pinning different versions in one file or counting one glob
    # differently, citing every site. Layer 0 lying-signal evidence.
    _spec("doc_claims", scan_doc_claims),
    # Guardrails stated (issue #767): the gates the repository configures (ruff or
    # flake8 complexity limit, the coverage_gate floor, pre-commit hooks, mypy
    # strict), each checked against the graded instruction files; an unstated gate
    # carries a ready one-line snippet. stated_pct feeds the guardrails-stated row.
    # A scoped run walks the scope only, as coverage_gate does (issue #956).
    _spec("guardrails", scan_guardrails),
    # Practice pack checks (issue #1273, lib/pack_checks.py): each --pack check evaluated
    # offline against the repository's files, under pack.<pack>.<check>, and each pack's
    # name, version and sha256 under packs. A spread entry that writes neither key without
    # --pack, so such a run's run-context.json is unchanged. Before level, which reads pack.
    _spec("pack_checks", collect_packs, spread=(PACK_BLOCK, IDENTITY_BLOCK)),
)


# The verified tally and the autonomy Step (lib/level_tally.py, #744): the run's
# maturity model (the default, or the --profile / --levels choice, #1150) over the
# finished run context. Core-only: the
# headless runner never reports it; /fleet computes the same from each row.
# core_scans.level zips its blocks with LEVEL_READS, so checks/core/level.toml must list
# exactly those reads in that order, then maturity_model: checked here, at import, on every run.
SCANS = (*SCANS, pinned_reads(_spec("level", cs.level), cs.LEVEL_ENTRY_READS))
# What changed since the previous run (lib/since_last_run.py, #1083): actions closed with their
# commits, gates gained or lost, the next blocker. Reads the run-context the previous run left,
# so it runs before the publish step overwrites it; the report and the log.md entry open with it.
SCANS = (*SCANS, _spec("since_last_run", cs.since_last_run))
# Monorepo unit readings (lib/unit_readings.py, #863): the scoped and override scans again
# for each unit of the units block, every condition tagged unit or inherited. The headless
# runner reports it under --units (signals.UNIT_SCANS, #866).
# core_scans.unit_readings splits its arguments by position, so the manifest reads are pinned.
SCANS = (*SCANS, pinned_reads(_spec("unit_readings", cs.unit_readings),
                              (*UNIT_OWN_READS, *UNIT_EXTERNAL_READS)))
# Each unit's Step and tally (lib/unit_levels.py, #864): the level engine over the run
# context with each unit's own readings in place. Core-only; reads pinned by position.
SCANS = (*SCANS, pinned_reads(_spec("unit_levels", cs.unit_levels), UNIT_LEVEL_READS))
# The repository roll-up over the units and the unit-unlock ranking (lib/unit_rollup.py,
# #865): the lowest unit's Step plus "N of M measured", and the changes ranked by the
# units they lift. Core-only.
SCANS = (*SCANS, _spec("unit_rollup", cs.unit_rollup))
# The deterministic headline (lib/headline.py, #747, #844, #1083): catalogue coverage and the per-tag
# strip, worded from the level block, and the badge and log entry summary. It carries the report's top
# card (lib/top_card.py, #1252), which reads since_last_run, unit_rollup and measured_commit, so it
# runs after the unit roll-up.
SCANS = (*SCANS, _spec("headline", cs.headline))

validate(SCANS)

# The unit pass (lib/unit_readings.py, #863), bound here so that scan module never imports
# the registry. The table is built on first use: the headless variants it reads import
# this module, so they cannot be imported while it loads.
def _unit_table() -> UnitTable:
    from lib.headless_layers import HEADLESS_SCANS as layer_scans
    from lib.headless_static import HEADLESS_SCANS as static_scans
    return unit_table(core_manifest(), SCANS, (*layer_scans, *static_scans))


bind_unit_pass(_unit_table, run_scans)
