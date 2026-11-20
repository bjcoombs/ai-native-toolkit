"""Optional fleet repository labels: a post-gather step outside the stamped run (issue #572).

A label (``service``, ``library``, ``infrastructure``, ``mirror``, ``docs`` or
``other``) says what kind of repository a ``repos.jsonl`` row is, so the fleet
views can group or filter by it. A small model chooses the label; this module
holds every deterministic part around that choice, so the model is the only
unverifiable step:

- ``digest`` reduces a ``FleetRow`` to the compact facts the model reads (the
  row only, never source), and ``build_prompt`` wraps a batch of digests in the
  fixed instructions.
- ``parse_answers`` reads the model's reply tolerantly (code fences and prose
  around the JSON lines are skipped).
- ``build_label_rows`` joins the answers to ``repos.jsonl``: exactly one row
  per repository, a label outside the closed set becomes ``other``, a missing
  answer becomes ``other`` with confidence 0, answers for unknown paths are
  dropped, the first answer for a path wins. ``model`` is the caller's model
  id, never one the model reports.
- ``write_labels`` writes ``labels.jsonl`` beside ``repos.jsonl``, sorted by
  path with sorted keys, so the same answers give the same bytes.
- ``check_labels`` validates a written file against ``repos.jsonl``.
- ``labels_by_path`` and ``filter_rows`` are the only read path: display and
  filter. Labels never enter a ``FleetRow``, a maturity model ``Group`` or any score;
  ``tests/test_fleet_labels.py`` pins that nothing in the gathering or scoring
  modules imports this one.

No model call, SDK or network here: the skill (``skills/fleet/SKILL.md``) runs
the model as a Haiku subagent and hands its reply to ``fleet_labels.py write``.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

if __package__ in (None, ""):  # run as a script to regenerate the schema: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.fleet import REPOS_FILE, FleetRow  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

LABELS_FILE = "labels.jsonl"
OTHER = "other"
# The closed set. Order is the order the prompt lists them.
LABELS = ("service", "library", "infrastructure", "mirror", "docs", OTHER)
LABEL_DEFINITIONS = {
    "service": "a deployable application, API, worker or job that runs somewhere",
    "library": "a package or module other code depends on; not deployed on its own",
    "infrastructure": "infrastructure as code, deployment or platform configuration, CI templates, "
                      "shared build tooling",
    "mirror": "a copy or fork of an upstream project kept in sync, with little development of its own",
    "docs": "mostly documentation, a handbook or a documentation website",
    OTHER: "anything else, or not enough evidence to choose",
}
DEFAULT_BATCH_SIZE = 25

# digest limits: deep enough to keep manifest kinds and counts, small enough
# that a batch of rows fits one small-model call.
_MAX_DEPTH = 3
_MAX_LIST = 8
_MAX_STR = 80
_FENCE = re.compile(r"^```[a-zA-Z]*$")


class LabelRow(TypedDict):
    path: str  # the repos.jsonl ``repo`` id: the join key, never an absolute path
    label: str  # one of LABELS
    confidence: float  # 0..1, the model's own; 0 when it gave none
    model: str  # the model id the caller ran


class LabelReport(TypedDict):
    labelled: int  # rows with a valid answer
    missing: list[str]  # paths with no answer: written as ``other``, confidence 0
    unknown: list[str]  # answer paths not in repos.jsonl: dropped
    duplicates: list[str]  # paths answered more than once: first answer kept
    coerced: list[str]  # paths whose label was outside the set: written as ``other``


class LabelsError(ValueError):
    """An input file cannot be read as the format it should be."""


# --- reading repos.jsonl ----------------------------------------------------


def read_repos(path: Path) -> list[FleetRow]:
    """The rows of a ``repos.jsonl``; only ``repo`` is required to be a string."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise LabelsError(f"cannot read {path}: {e.strerror}") from e
    rows: list[FleetRow] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise LabelsError(f"{path.name} line {number}: not JSON ({e.msg})") from e
        if not isinstance(row, dict) or not isinstance(row.get("repo"), str):
            raise LabelsError(f"{path.name} line {number}: not a fleet row (no repo id)")
        rows.append(row)  # type: ignore[arg-type]
    return rows


# --- model input --------------------------------------------------------------


def _flatten(value: Any, prefix: str, depth: int, out: JsonDict) -> None:
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            out[f"{prefix}#keys"] = len(value)
            return
        for key in sorted(value):
            if key == "available" and value[key] is True:
                continue
            _flatten(value[key], f"{prefix}.{key}" if prefix else str(key), depth + 1, out)
    elif isinstance(value, list):
        scalars = all(isinstance(v, (bool, int, float, str)) for v in value)
        if scalars and 0 < len(value) <= _MAX_LIST and all(
            not isinstance(v, str) or len(v) <= _MAX_STR for v in value
        ):
            out[prefix] = list(value)
        elif value:
            out[f"{prefix}#len"] = len(value)
            # manifest-like lists: keep the distinct short kind tokens
            kinds = sorted({str(v[k]) for v in value if isinstance(v, dict)
                            for k in ("ecosystem", "kind") if isinstance(v.get(k), str)})
            if kinds:
                out[f"{prefix}#kinds"] = kinds[:_MAX_LIST]
    elif value is None or isinstance(value, (bool, int, float)):
        if value is not None:
            out[prefix] = value
    elif isinstance(value, str) and len(value) <= _MAX_STR:
        out[prefix] = value


def digest(row: Mapping[str, Any]) -> JsonDict:
    """The compact facts the model reads for one row: identity plus scalar signal leaves.

    Unavailable scan blocks are left out; lists become their length (short scalar
    lists are kept); nesting stops at a fixed depth. Deterministic.
    """
    facts: JsonDict = {}
    scans = row.get("scans")
    if isinstance(scans, dict):
        for name in sorted(scans):
            block = scans[name]
            if not isinstance(block, dict) or block.get("available") is False:
                continue
            flat: JsonDict = {}
            _flatten(block, "", 0, flat)
            if flat:
                facts[name] = flat
    return {
        "path": row["repo"],
        "group": row.get("group"),
        "status": row.get("status"),
        "facts": facts,
    }


def batches(rows: Sequence[FleetRow], size: int) -> list[list[FleetRow]]:
    """``rows`` sorted by id, cut into batches of at most ``size``."""
    if size < 1:
        raise ValueError("batch size must be at least 1")
    ordered = sorted(rows, key=lambda r: r["repo"])
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def build_prompt(rows: Iterable[FleetRow]) -> str:
    """The full instruction text for one batch: definitions, output format, then one digest per line."""
    lines = [
        "Label each repository below with exactly one label from this closed set:",
        "",
        *(f"- {label}: {LABEL_DEFINITIONS[label]}" for label in LABELS),
        "",
        "Each input line is one repository: its path (owner or group, then name), its status,",
        "and counts-only facts gathered from it. Judge from the path and the facts only; you",
        "have no other access to the repository. A failed row has no facts: use its path, or",
        f"answer {OTHER}.",
        "",
        "Reply with one JSON object per line and nothing else, one line per input repository,",
        'in this shape: {"path": "<path exactly as given>", "label": "<label>", "confidence": <0.0-1.0>}',
        "",
        "Repositories:",
        *(json.dumps(digest(row), sort_keys=True, separators=(",", ":")) for row in rows),
    ]
    return "\n".join(lines) + "\n"


# --- model output -------------------------------------------------------------


def parse_answers(text: str) -> list[JsonDict]:
    """JSON objects from a model reply: a JSON array, or one object (or one-line array) per line amid fences and prose."""
    stripped = text.strip()
    try:
        whole = json.loads(stripped)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, list):
        return [item for item in whole if isinstance(item, dict)]
    if isinstance(whole, dict):
        return [whole]
    answers: list[JsonDict] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or _FENCE.match(line) or not line.startswith(("{", "[")):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            answers.append(item)
        elif isinstance(item, list):  # one batch replied as a one-line array
            answers.extend(i for i in item if isinstance(i, dict))
    return answers


def normalise_label(raw: Any) -> str | None:
    """The label in the closed set, case and whitespace ignored; ``None`` when outside it."""
    if not isinstance(raw, str):
        return None
    label = raw.strip().lower()
    return label if label in LABELS else None


def normalise_confidence(raw: Any) -> float | None:
    """A finite number in [0, 1], rounded to 2 places; ``None`` for anything else (bools included)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return round(value, 2)


def build_label_rows(
    repos: Iterable[FleetRow], answers: Iterable[Mapping[str, Any]], model: str,
) -> tuple[list[LabelRow], LabelReport]:
    """One ``LabelRow`` per repository, sorted by path, and what was coerced on the way."""
    if not model.strip():
        raise ValueError("model id must be non-empty")
    paths = sorted({row["repo"] for row in repos})
    known = set(paths)
    chosen: dict[str, Mapping[str, Any]] = {}
    unknown: set[str] = set()
    duplicates: set[str] = set()
    for answer in answers:
        path = answer.get("path")
        if not isinstance(path, str):
            continue
        if path not in known:
            unknown.add(path)
        elif path in chosen:
            duplicates.add(path)
        else:
            chosen[path] = answer
    rows: list[LabelRow] = []
    missing: list[str] = []
    coerced: list[str] = []
    for path in paths:
        picked = chosen.get(path)
        if picked is None:
            missing.append(path)
            rows.append({"path": path, "label": OTHER, "confidence": 0.0, "model": model})
            continue
        label = normalise_label(picked.get("label"))
        if label is None:
            coerced.append(path)
        rows.append({
            "path": path,
            "label": label or OTHER,
            "confidence": normalise_confidence(picked.get("confidence")) or 0.0,
            "model": model,
        })
    report: LabelReport = {
        "labelled": len(paths) - len(missing),
        "missing": missing,
        "unknown": sorted(unknown),
        "duplicates": sorted(duplicates),
        "coerced": coerced,
    }
    return rows, report


# --- writing and checking -----------------------------------------------------


def dumps_labels(rows: Iterable[LabelRow]) -> str:
    ordered = sorted(rows, key=lambda r: r["path"])
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in ordered)


def write_labels(out_dir: Path, rows: Iterable[LabelRow]) -> Path:
    """Write ``<out_dir>/labels.jsonl`` atomically. ``repos.jsonl`` is never opened for writing."""
    assert LABELS_FILE != REPOS_FILE
    target = out_dir / LABELS_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{LABELS_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps_labels(rows))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


_ROW_KEYS = frozenset(LabelRow.__annotations__)


def _row_problems(row: Any, where: str) -> list[str]:
    if not isinstance(row, dict):
        return [f"{where}: not a JSON object"]
    problems = []
    if set(row) != _ROW_KEYS:
        problems.append(f"{where}: keys {sorted(row)} are not {sorted(_ROW_KEYS)}")
    if not isinstance(row.get("path"), str) or not row["path"]:
        problems.append(f"{where}: path is not a non-empty string")
    if row.get("label") not in LABELS:
        problems.append(f"{where}: label {row.get('label')!r} is not one of {', '.join(LABELS)}")
    confidence = row.get("confidence")
    # Same rule as the schema's ``"type": "number", 0..1``: an integer 0 or 1 is valid.
    if normalise_confidence(confidence) is None:
        problems.append(f"{where}: confidence {confidence!r} is not a number in [0, 1]")
    if not isinstance(row.get("model"), str) or not row["model"].strip():
        problems.append(f"{where}: model is not a non-empty string")
    return problems


def check_labels(labels_text: str, repo_ids: Iterable[str]) -> list[str]:
    """Every way ``labels_text`` is not a valid ``labels.jsonl`` for these repositories; empty when valid."""
    expected = sorted(set(repo_ids))
    problems: list[str] = []
    seen: list[str] = []
    for number, line in enumerate(labels_text.splitlines(), 1):
        where = f"line {number}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{where}: not JSON")
            continue
        problems.extend(_row_problems(row, where))
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            seen.append(row["path"])
    if seen != sorted(seen):
        problems.append("rows are not sorted by path")
    for path in sorted({p for p in seen if seen.count(p) > 1}):
        problems.append(f"{path}: more than one row")
    for path in sorted(set(seen) - set(expected)):
        problems.append(f"{path}: not in {REPOS_FILE}")
    for path in sorted(set(expected) - set(seen)):
        problems.append(f"{path}: no row")
    return problems


# --- reading: display and filter only -----------------------------------------


def read_labels(path: Path) -> list[LabelRow]:
    """The rows of a ``labels.jsonl`` that are individually valid; others are skipped."""
    rows: list[LabelRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _row_problems(row, ""):
            rows.append(row)
    return rows


def labels_by_path(repos: Iterable[FleetRow], labels: Iterable[LabelRow]) -> dict[str, str | None]:
    """Each repository's label, ``None`` when it has none. Labels for unknown paths are ignored."""
    by_path: dict[str, str] = {}
    for row in labels:
        by_path.setdefault(row["path"], row["label"])
    return {row["repo"]: by_path.get(row["repo"]) for row in repos}


def filter_rows(
    repos: Iterable[FleetRow], labels: Iterable[LabelRow], wanted: Iterable[str],
) -> list[FleetRow]:
    """The rows whose label is in ``wanted``, unchanged. A row with no label never matches."""
    repo_list = list(repos)
    keep = set(wanted)
    by_path = labels_by_path(repo_list, labels)
    return [row for row in repo_list if by_path[row["repo"]] in keep]


# --- published schema ---------------------------------------------------------

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "fleet" / "references" / "labels.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/labels.schema.json"


def build_schema() -> JsonDict:
    """The ``labels.jsonl`` row schema; its keys are checked against ``LabelRow``."""
    props: JsonDict = {
        "path": {"type": "string", "minLength": 1},
        "label": {"enum": list(LABELS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "model": {"type": "string", "minLength": 1},
    }
    assert set(props) == _ROW_KEYS  # the schema follows LabelRow
    schema: JsonDict = {
        "type": "object",
        "properties": props,
        "required": sorted(LabelRow.__required_keys__),
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet repository label row",
        "description": (
            "One line of labels.jsonl written by skills/fleet/scripts/fleet_labels.py beside "
            "repos.jsonl. path is the repos.jsonl repo id. Display and filter only; never "
            "scored. Generated from lib/fleet_labels.py."
        ),
        **schema,
    }


def render_schema() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":  # pragma: no cover - regeneration entry point
    SCHEMA_PATH.write_text(render_schema(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
