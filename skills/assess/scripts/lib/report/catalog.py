"""The measures catalog: what ``/assess`` and ``/fleet`` measure, generated from the check manifest (#794).

``build_catalog`` joins the check manifest (``checks/core.toml`` and its fragments under
``checks/core/``, ``lib.manifest.check_manifest``),
the default maturity model (``levels/default.toml``) and the signal input classes
(``lib.scoring.level_signals``, #783) into one ``Catalog`` document. ``lib.report.catalog_md`` renders it
as ``docs/what-we-measure.md``; ``render_json`` writes it as
``skills/assess/references/measures.json``. ``gen_catalog.py`` writes both, and
``tests/test_catalog.py`` fails when either committed copy is stale. Pure and
deterministic: no date, commit, version string or absolute path is read, so only a
change to the manifest, the model or the signal classes changes the output.

Shape (``CATALOG_VERSION`` 1). Every entry is an object and every relation sits under a
named key, so a later field (the #939 ``why``) is an added key, never a restructure:

- ``grades``: each confidence grade, the input classes that give it, and whether a
  condition at that grade counts toward a level (``measured`` and ``verified`` do;
  ``config_only`` and ``declared`` are listed, not counted).
- ``rows``: each maturity-model row with its levels. A level is one condition: its ``id``
  (``<row id>.<level>``), ``truth`` tags, ``polarity``, ``requires``, ``guarded_by``, the exit
  rule's ``predicted`` direction and ``review`` date (null when unset, #1086), the
  ``step_gates`` that cover it, its ``grade`` (the weakest of every signal its criteria read,
  ``eq_signal`` included, as ``lib.scoring.level_tally`` grades it) and whether it ``counts``. Each
  criterion has the signal, its ``producer`` (scan block and collector), the operator and
  value or ``eq_signal``, the plain-words criterion, the condition's title and next step from
  the manifest, its grade and whether it counts.
- ``tags``, ``tag_pairs``, ``steps`` and ``links``: the truth-tag frame (``lib.report.catalog_tags``,
  #870): the conditions under each tag, the conditions each pair of tags shares, the Step
  gates in order, and the requires, guard and equality links.
- ``scans``: one entry per scan name the headless runner accepts
  (``signals.known_scans()``, the ``assess_signals.py --scans`` list): the collector that
  builds it, what it reads, what its headless variant reads (``headless_reads``, the
  manifest's ``headless_variants`` entry; null without one, #918), whether it needs the
  network, how a headless or fleet run
  reports it (``units_only``: the ``signals.UNIT_SCANS``, #866), its counts-only privacy class, the rows, conditions and findings that use
  it, its unknown reasons (a reason declared with a ``block`` only on that block, #918), and
  the input class and grade of each signal it feeds. A scan's ``grades`` come only from the
  signals a criterion of the model reads, so a scan no criterion reads is ``unclassified``
  while its ``evidence`` keeps each signal's manifest class (#918); classifying every
  collector is #789 step 6.
- ``collectors``: every scan in the registry (``scan_registry.SCANS``), including the
  private inputs other scans read.
- ``signals``: each signal a criterion reads, with its type, collector, input class and
  grade.
- ``findings``: each derived finding, with its inputs, rules, declared ``thresholds`` (#918)
  and report ``order``.

**Registry check (#918).** ``build_catalog`` joins the manifest against the installed scan
registry (``signals.known_scans()``). Before the join, ``registry_errors`` lists each scan no
collector of the manifest publishes, and a supplied manifest that misses one raises
``CheckManifestError`` with one ``UNPUBLISHED_SCAN`` error per scan, never a ``KeyError``.

**Stable order (#1239).** Every list a new check adds to is sorted by a stable key: rows by
id (``stable_model``, so tag lists and links follow condition ids), scans, collectors and
findings by name, signals by path, links by source. A check's entries land at a fixed place
whatever order its sources declare them in, so two PRs that add different checks change
different lines of the generated files and merge cleanly. No count is written.

(#789 step 2, #794)
`build_catalog(manifest=None, model=None)` joins the check manifest (`checks/core.toml` and its
fragments), the default maturity model and `level_signals.INPUT_GRADES` into one `Catalog` (`CATALOG_VERSION`
1); `render_json` writes it as `skills/assess/references/measures.json`. Keys: `grades` (each
grade, its input classes and whether it `counts`: `measured` and `verified` do), `rows` (each
row's levels, each one condition with its `id`, `truth`, `polarity`, `requires`, `guarded_by`,
`predicted`, `review` (#1086),
`step_gates`, `grade` and `counts`, and its criteria with the condition `key`, `producer`, `eq_signal`, plain-words `text`, `title` and `fix` from
the manifest, `input_class`, `grade`, `counts`), `scans` (one per `signals.known_scans()` name:
`collector`, `reads`, `headless_reads`, `network`, `platform`, `headless` (`registered`, `headless_variant`,
`unregistered` or `platform_only`), `privacy` (`written`, `strings_kept`: the collector's
counts-only patterns, split by block for a spread collector), `used_by` (`rows`, `conditions`,
`findings`), `unknown_reasons` (the generic `not_registered`, `network` or `platform_only`
reason plus the manifest's declared ones), `evidence` per signal and `grades`), `collectors`
(every `scan_registry.SCANS` entry), `signals` (sorted by path) and `findings` (sorted by name, with `thresholds`). `sources` (`catalog.SOURCES`) lists the
files it is generated from, repo-relative; `skills/assess/checks/core/*.toml` names the
manifest fragments as a glob (#994). A scan no criterion reads is
graded `unclassified` until #789 step 6 classifies every collector. Every entry is an object and
every relation a named key. Each criterion, scan, finding and tag carries `why` and `why_source`
from the manifest (`lib.manifest.manifest_whys`, #939): `""` where a version 1 manifest has none, and a
scan's `why` may be the token `exempt: plumbing`; signals carry none. `tags`, `tag_pairs`,
`steps` and `links` come from `lib.report.catalog_tags`. No date,
commit or version string is read, so a version-only bump of the model leaves it unchanged.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Literal, NotRequired, TypedDict

from lib.report import catalog_tags as ct
import lib.manifest.check_manifest as cm
import lib.manifest.manifest_whys as mw
from lib.manifest.check_validation import ValidationError, finding
from lib.scoring.level_signals import INPUT_GRADES, counts_toward_level, load_default_levels
from lib.scoring.levels import Criterion, MaturityModel, Scalar, condition_id
from lib.scoring.levels_fields import DEFAULT_POLARITY, TRUTH_TAGS
from lib.run.signals import NETWORK_REASON, PLATFORM_ONLY_SCANS, PLATFORM_SCANS, UNIT_SCANS, known_scans

CATALOG_VERSION = 1
GRADE_ORDER = ("config_only", "declared", "measured", "verified")
UNCLASSIFIED = "unclassified"
# The sources the catalog is generated from, repo-relative; a glob names a set of files
# (the check manifest's fragments, #994).
SOURCES = ("skills/assess/checks/core.toml", "skills/assess/checks/core/*.toml",
           "skills/assess/levels/default.toml",
           "skills/assess/scripts/lib/scoring/level_signals.py", "skills/assess/scripts/lib/run/signals.py",
           "skills/assess/scripts/lib/run/scan_registry.py")

Headless = Literal["registered", "headless_variant", "unregistered", "platform_only", "units_only"]

# Plain words for each maturity-model operator.
OP_WORDS = {"eq": "is", "ne": "is not", "lt": "is below", "lte": "is at most",
            "gt": "is above", "gte": "is at least", "in": "is one of", "not_in": "is not one of"}

GENERIC_REASONS: dict[str, tuple[str, str]] = {
    "not_registered": (
        "Only a full /assess run builds this block; a headless or fleet run reports it "
        "not measured.",
        "Run /assess on the repository to measure it."),
    "network_platform": (
        "It reads GitHub or GitLab, and a run makes no network call without --platform.",
        "Run the fleet or assess_signals.py with --platform, with gh or glab signed in."),
    "network_full": (
        "It reads GitHub or GitLab, which only a full /assess run does.",
        "Run /assess on the repository with gh or glab signed in."),
    "platform_only": (
        "The block exists only on the platform: without --platform it is left out.",
        "Run the fleet or assess_signals.py with --platform, with gh or glab signed in."),
}


class GradeInfo(TypedDict):
    grade: str
    input_classes: list[str]
    counts: bool


class CatalogReason(TypedDict):
    code: str
    means: str
    next_step: str
    carried_in: NotRequired[str]


class Evidence(TypedDict):
    signal: str
    input_class: str
    grade: str


class Privacy(TypedDict):
    written: bool  # False for a private collector: an input to other scans, never output
    strings_kept: dict[str, str]  # field pattern -> class (path, token, reason, id, drop)


class UsedBy(TypedDict):
    rows: list[str]
    conditions: list[str]
    findings: list[str]


class Producer(TypedDict):
    scan: str  # the run-context block the signal is read from
    collector: str  # the manifest collector that writes it, or "" when no signal entry names one


class CatalogCriterion(TypedDict):
    key: str
    signal: str
    producer: Producer
    op: str
    value: Scalar | list[Scalar]
    eq_signal: str | None  # the second signal an equality criterion compares with, else None
    text: str
    title: str
    fix: str
    why: str  # the condition's why from the manifest (#939); "" with no manifest entry
    why_source: str
    input_class: str
    grade: str
    counts: bool


class CatalogLevel(TypedDict):
    level: int
    id: str  # the condition id, <row id>.<level>
    truth: list[str]  # the condition's truth tags (lib/scoring/levels_fields.py, #843)
    polarity: str  # positive or hazard
    requires: list[str]
    guarded_by: str | None
    predicted: str | None  # the exit rule's predicted direction (lib/scoring/levels_fields.py, #1086)
    review: str | None  # its review date, ISO; None when the condition has none
    step_gates: list[str]  # the gate ids that cover this condition, in Step order
    grade: str
    counts: bool
    criteria: list[CatalogCriterion]


class CatalogRow(TypedDict):
    id: str
    title: str
    scope: str
    aggregation: str
    levels: list[CatalogLevel]


class CatalogScan(TypedDict):
    name: str
    title: str
    summary: str
    collector: str
    reads: list[str]
    headless_reads: list[str] | None  # the headless variant's manifest reads; None without a variant
    network: bool
    platform: bool
    headless: Headless
    privacy: Privacy
    used_by: UsedBy
    unknown_reasons: list[CatalogReason]
    evidence: list[Evidence]
    grades: list[str]
    why: str  # the scan's why, or exactly "exempt: plumbing" (#939)
    why_source: str


class CatalogCollector(TypedDict):
    name: str
    title: str
    publishes: list[str]
    reads: list[str]
    network: bool
    private: bool


class CatalogSignal(TypedDict):
    path: str
    title: str
    type: str
    collector: str
    input_class: str
    grade: str
    null_means: str


class CatalogFinding(TypedDict):
    name: str
    title: str
    order: int
    action: str
    mode: str
    concern: bool
    inputs: list[str]
    suppressed_when: str
    prune_missing_paths: bool
    thresholds: dict[str, float]  # the manifest's declared thresholds, sorted by name; {} when none
    why: str
    why_source: str


class Catalog(TypedDict):
    catalog_version: int
    sources: list[str]
    grades: list[GradeInfo]
    rows: list[CatalogRow]
    tags: list[ct.CatalogTag]
    tag_pairs: list[ct.TagPair]
    steps: list[ct.CatalogStep]
    links: list[ct.CatalogLink]
    scans: list[CatalogScan]
    collectors: list[CatalogCollector]
    signals: list[CatalogSignal]
    findings: list[CatalogFinding]


def grade_of(input_class: str) -> str:
    """The grade of a producer input class, or ``unclassified`` for an unknown one."""
    return next((g for c, g in INPUT_GRADES.items() if c == input_class), UNCLASSIFIED)


def counts(grade: str) -> bool:
    """True when a condition at ``grade`` counts toward a level (``level_signals.counts_toward_level``)."""
    return counts_toward_level(grade)


def _grades() -> list[GradeInfo]:
    return [GradeInfo(grade=g, input_classes=sorted(c for c, cg in INPUT_GRADES.items() if cg == g),
                      counts=counts(g)) for g in GRADE_ORDER]


def _value_words(value: Scalar | list[Scalar]) -> str:
    if isinstance(value, list):
        return ", ".join(_value_words(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def criterion_text(criterion: Criterion, title: str) -> str:
    """The criterion in plain words: the signal's title, the operator, the value."""
    if "eq_signal" in criterion:  # two measured signals compared (lib/scoring/levels_fields.py)
        return f"{title}: {OP_WORDS.get(criterion['op'], criterion['op'])} signal {criterion['eq_signal']}"
    return f"{title}: {OP_WORDS.get(criterion['op'], criterion['op'])} {_value_words(criterion['value'])}"


def _weakest(grades: Sequence[str]) -> str:
    """The weakest grade, or ``unclassified`` when any signal is unclassified (``level_tally._grade``)."""
    if not grades or UNCLASSIFIED in grades:
        return UNCLASSIFIED
    return min(grades, key=GRADE_ORDER.index)


def _signal_grade(path: str, signals: Mapping[str, cm.SignalEntry]) -> str:
    signal = signals.get(path)
    return grade_of(signal["evidence"]) if signal else UNCLASSIFIED


def _rows(model: MaturityModel, signals: Mapping[str, cm.SignalEntry],
          conditions: Mapping[str, cm.ConditionEntry],
          gated: Mapping[str, list[tuple[str, int]]]) -> list[CatalogRow]:
    rows: list[CatalogRow] = []
    for row in model["rows"]:
        levels: list[CatalogLevel] = []
        for level in row["levels"]:
            criteria: list[CatalogCriterion] = []
            paths: list[str] = []
            for criterion in level["criteria"]:
                paths += [criterion["signal"], *([criterion["eq_signal"]] if "eq_signal" in criterion else [])]
                key = cm.condition_key(row["id"], level["level"], criterion["signal"])
                signal = signals.get(criterion["signal"])
                cond = conditions.get(key)
                input_class = signal["evidence"] if signal else UNCLASSIFIED
                grade = grade_of(input_class)
                title = signal["title"] if signal else criterion["signal"]
                criteria.append(CatalogCriterion(
                    key=key, signal=criterion["signal"],
                    producer=Producer(scan=_first(criterion["signal"]),
                                      collector=signal.get("collector", "") if signal else ""),
                    op=criterion["op"], value=criterion["value"], eq_signal=criterion.get("eq_signal"),
                    text=criterion_text(criterion, title),
                    title=cond["title"] if cond else title, fix=cond["fix"] if cond else "",
                    why=mw.why_of(cond) if cond else "", why_source=mw.why_source_of(cond) if cond else "",
                    input_class=input_class, grade=grade,
                    counts=counts(grade) and level.get("polarity") != "hazard"))
            cid = condition_id(row["id"], level["level"])
            grade = _weakest([_signal_grade(p, signals) for p in paths])
            hazard = level.get("polarity") == "hazard"
            levels.append(CatalogLevel(
                level=level["level"], id=cid, truth=list(level["truth"]),
                polarity="hazard" if hazard else DEFAULT_POLARITY, requires=list(level.get("requires", [])),
                guarded_by=level.get("guarded_by"), predicted=level.get("predicted"),
                review=level.get("review"), step_gates=[g for g, _ in gated.get(cid, [])],
                grade=grade, counts=counts(grade) and not hazard, criteria=criteria))
        agg = row["aggregation"]
        rows.append(CatalogRow(id=row["id"], title=row["title"], scope=row["scope"],
                               aggregation=agg["rule"] if agg else "min", levels=levels))
    return rows


def _headless(name: str, collector: cm.CollectorEntry) -> Headless:
    if name in PLATFORM_ONLY_SCANS:
        return "platform_only"
    if name in UNIT_SCANS:
        return "units_only"
    status = collector.get("headless", {}).get(name)
    if status == "registered":
        return "registered"
    if status == "headless_variant":
        return "headless_variant"
    return "unregistered"


def _reasons(name: str, collector: cm.CollectorEntry, headless: Headless) -> list[CatalogReason]:
    codes: list[tuple[str, str]] = []  # (generic key, reported code)
    if headless == "unregistered":
        codes.append(("not_registered", "not_registered"))
    if headless == "platform_only":
        codes.append(("platform_only", "platform_only"))
    elif collector.get("network") and "offline_callable" not in collector:
        codes.append(("network_platform" if name in PLATFORM_SCANS else "network_full",
                      NETWORK_REASON.split(":", 1)[0]))
    out = [CatalogReason(code=code, means=GENERIC_REASONS[key][0], next_step=GENERIC_REASONS[key][1])
           for key, code in codes]
    for declared in collector.get("unknown_reasons", []):
        if declared.get("block", name) != name:  # a reason scoped to another block of the collector
            continue
        reason = CatalogReason(code=declared["code"], means=declared["means"],
                               next_step=declared["next_step"])
        if "carried_in" in declared:
            reason["carried_in"] = declared["carried_in"]
        out.append(reason)
    return out


def _privacy(name: str, collector: cm.CollectorEntry, spread: bool) -> Privacy:
    table = collector.get("privacy", {})
    if spread:
        table = {k: v for k, v in table.items() if k == name or k.startswith(f"{name}.")}
    return Privacy(written=not collector.get("private", False), strings_kept=dict(sorted(table.items())))


def _first(path: str) -> str:
    return path.split(".", 1)[0]


def _used_by(names: set[str], rows: Sequence[CatalogRow],
             findings: Sequence[cm.FindingEntry]) -> UsedBy:
    crits = [(row["id"], c) for row in rows for lv in row["levels"] for c in lv["criteria"]
             if _first(c["signal"]) in names]
    return UsedBy(rows=sorted({r for r, _ in crits}), conditions=[c["key"] for _, c in crits],
                  findings=[f["name"] for f in findings if any(_first(i) in names for i in f["inputs"])])


def registry_errors(manifest: cm.CheckManifest) -> list[ValidationError]:
    """One ``UNPUBLISHED_SCAN`` error per scan the headless runner accepts that no collector of
    ``manifest`` publishes: an alternate manifest must cover the installed registry before the join."""
    published = {block for c in cm.collectors(manifest) for block in c["publishes"]}
    return [finding(f"scans.{name}", "UNPUBLISHED_SCAN",
                    f"no collector publishes {name!r}, a scan the installed registry runs")
            for name in known_scans() if name not in published]


def _read_signals(rows: Sequence[CatalogRow]) -> set[str]:
    """Every signal path a criterion of the model reads, ``eq_signal`` included."""
    return {p for row in rows for lv in row["levels"] for c in lv["criteria"]
            for p in (c["signal"], *([c["eq_signal"]] if c["eq_signal"] is not None else []))}


def _headless_reads(name: str, collector: cm.CollectorEntry) -> list[str] | None:
    return next((list(v["reads"]) for v in collector.get("headless_variants", []) if v["block"] == name), None)


def _scans(manifest: cm.CheckManifest, rows: Sequence[CatalogRow],
           signals: Sequence[cm.SignalEntry]) -> list[CatalogScan]:
    by_block = {block: c for c in cm.collectors(manifest) for block in c["publishes"]}
    findings = cm.findings(manifest)
    read = _read_signals(rows)
    out: list[CatalogScan] = []
    for name in known_scans():
        collector = by_block[name]
        spread = bool(collector.get("spread"))
        headless = _headless(name, collector)
        names = {name} if spread else {name, cm.scan_key(collector)}
        evidence = [Evidence(signal=s["path"], input_class=s["evidence"],
                             grade=grade_of(s["evidence"]))
                    for s in signals if _first(s["path"]) == name]
        # Graded only by the signals a criterion reads; every signal stays as evidence detail.
        grades = [g for g in GRADE_ORDER
                  if any(e["grade"] == g and e["signal"] in read for e in evidence)] or [UNCLASSIFIED]
        out.append(CatalogScan(
            name=name, title=collector["title"] if not spread else f"{collector['title']}: {name}",
            summary=collector.get("summary", ""), collector=collector["id"],
            reads=list(collector["reads"]), headless_reads=_headless_reads(name, collector),
            network=bool(collector.get("network")),
            platform=bool(collector.get("platform_pass")), headless=headless,
            privacy=_privacy(name, collector, spread), used_by=_used_by(names, rows, findings),
            unknown_reasons=_reasons(name, collector, headless), evidence=evidence, grades=grades,
            why=mw.scan_why(collector, name), why_source=mw.scan_why_source(collector, name)))
    return out


def stable_model(model: MaturityModel) -> MaturityModel:
    """``model`` with its rows sorted by id, so a new row lands at a fixed place (#1239).

    The catalog trades the model's read-side to write-side progression for this order on
    purpose: a row keyed by its id has one place whatever PR adds it, so parallel additions
    merge cleanly. ``levels/default.toml`` and every report keep the model's own order."""
    out = copy.copy(model)
    out["rows"] = sorted(model["rows"], key=lambda r: r["id"])
    return out


def build_catalog(manifest: cm.CheckManifest | None = None,
                  model: MaturityModel | None = None) -> Catalog:
    """The catalog of the core manifest and the default maturity model (or the given ones)."""
    manifest = cm.load_manifest() if manifest is None else manifest
    if errors := registry_errors(manifest):
        raise cm.CheckManifestError("check manifest against the installed registry", errors)
    model = load_default_levels() if model is None else model
    model = stable_model(model)
    signals = sorted(cm.signals(manifest), key=lambda s: s["path"])
    by_path = {s["path"]: s for s in signals}
    truth = {condition_id(r["id"], lv["level"]): list(lv["truth"]) for r in model["rows"] for lv in r["levels"]}
    steps = ct.steps(model, truth)
    rows = _rows(model, by_path, {c["key"]: c for c in cm.conditions(manifest)}, ct.gates_by_condition(steps))
    levels = [lv for r in rows for lv in r["levels"]]
    return Catalog(
        catalog_version=CATALOG_VERSION,
        sources=list(SOURCES),
        grades=_grades(),
        rows=rows,
        tags=ct.tags(levels, {t: (mw.tag_why(manifest, t)["why"], mw.tag_why(manifest, t).get("why_source", ""))
                              for t in TRUTH_TAGS}),
        tag_pairs=ct.tag_pairs(levels),
        steps=steps,
        links=sorted(ct.links(model), key=lambda link: (link["source"], link["kind"], link["target"])),
        scans=sorted(_scans(manifest, rows, signals), key=lambda s: s["name"]),
        collectors=sorted((CatalogCollector(name=cm.scan_key(c), title=c["title"], publishes=list(c["publishes"]),
                                            reads=list(c["reads"]), network=bool(c.get("network")),
                                            private=bool(c.get("private"))) for c in cm.collectors(manifest)),
                          key=lambda c: c["name"]),
        signals=[CatalogSignal(path=s["path"], title=s["title"], type=s["type"], collector=s["collector"],
                               input_class=s["evidence"],
                               grade=grade_of(s["evidence"]),
                               null_means=s.get("null_means", "")) for s in signals],
        findings=[CatalogFinding(name=f["name"], title=f["title"], order=f["order"], action=f["action"],
                                 mode=f["mode"], concern=f["concern"], inputs=list(f["inputs"]),
                                 suppressed_when=f.get("suppressed_when", ""),
                                 prune_missing_paths=bool(f.get("prune_missing_paths")),
                                 thresholds=dict(sorted(f.get("thresholds", {}).items())),
                                 why=mw.why_of(f), why_source=mw.why_source_of(f))
                  for f in sorted(cm.findings(manifest), key=lambda f: f["name"])],
    )


def render_json(catalog: Catalog) -> str:
    """``measures.json``: the catalog as indented JSON with a trailing newline."""
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
