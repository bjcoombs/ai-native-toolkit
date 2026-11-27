#!/usr/bin/env python3
"""Compute a marathon's flow metrics from its tracking file and saved GitHub API responses.

Deterministic and offline: it reads files and writes one report, and never calls
the network. The lead saves the GitHub responses first (the commands are in
`references/flow-metrics.md`, next to this script's skill), then runs:

    python3 flow_metrics.py --tracking pr-tracking.json --github <dir> \
        [--previous <earlier-run.json>] [--format markdown|json]

Inputs:
- `--tracking`: the marathon's `pr-tracking.json`. Only its `flow` object is
  read: `started_at`, `ended_at`, `units` (`{"<unit>": {"pr": n, "spawned_at": t}}`),
  `events` (typed rows, see `EVENT_TYPES`), an optional `tokens.total` and
  optional `waves` (one row per wave: `dag_width`, `units`, `combines`, each
  `{"units": [...], "hot_files": [...]}`, and `sequential_hot_files`).
- `--github`: a directory holding, per PR number n, `n.pull.json`
  (`pulls/n`), `n.commits.json` (`pulls/n/commits`), `n.reviews.json`
  (`pulls/n/reviews`) and `n.runs.json` (`actions/runs?branch=<head>`, every
  page). Only `n.pull.json` is required; a missing file, or a runs file holding
  fewer runs than its `total_count`, makes the metrics that need it null.
- `--previous`: the JSON output of an earlier run, for the comparison column.

Definitions (each metric is null, never 0, when its input is missing):
- pushes: distinct head SHAs among the PR's workflow runs.
- merges of main: PR commits with two or more parents.
- review rounds: distinct commits that received a review.
- first green: the earliest head SHA, pushed after the last red one, on which
  every workflow that succeeded on the merged head also succeeded; the time is
  the latest of those runs' ends (see `first_green`).
- wait: first green to merged, in minutes.
- spawn to PR: the unit's `spawned_at` to the PR's `created_at`, in minutes.
- mean WIP: the time-weighted mean of open PRs over the run window.
- throughput: merged PRs per hour of the run window.
- units lost to combining: a wave's DAG width (units with no unmet
  dependency) minus its units after hot-file combining; the run total sums the
  waves and is null when any wave lacks either count.
- combining hot files: files ranked by the combines they caused. Files a wave
  lists in `sequential_hot_files` (a version counter, a lockfile) cost merge
  order, not combining, so they are ranked apart and never count as combining
  files in that wave.
  With no `waves` recorded, every combining metric is null (see `combining`).

Python 3.8 compatible and stdlib only, so it runs under any `python3`.

Lint: this file sits outside `make lint`, which gates only `skills/assess/` and
`scripts/`. Check it with that package's config from `scripts/`:
`uv run --locked ruff check ../skills/marathon/scripts/flow_metrics.py` and
`uv run --locked mypy --strict ../skills/marathon/scripts/flow_metrics.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

EVENT_TYPES = ("semantic_conflict", "recheck_failure", "contract_refreeze", "main_red", "load")

Json = Dict[str, Any]


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """An ISO 8601 UTC timestamp (GitHub's `Z` form included) as an aware datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def minutes(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 1)


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def first_green(runs: List[Json], head_sha: str) -> Optional[datetime]:
    """When the PR last became green and stayed green until merge.

    The required set is every workflow green at the merged head. A SHA is red
    when a required workflow failed on it and never succeeded on it. The result
    is the end time of the earliest fully green SHA pushed after the last red
    one, so review rework after an earlier green counts as work, not as wait.
    """
    required = {r["name"] for r in runs if r.get("head_sha") == head_sha and r.get("conclusion") == "success"}
    if not required:
        return None
    pushed: Dict[str, datetime] = {}
    green: Dict[str, Dict[str, datetime]] = {}
    failed: Dict[str, Set[str]] = {}
    for r in runs:
        ts = parse_ts(r.get("updated_at"))
        if ts is None:
            continue
        sha = r["head_sha"]
        started = parse_ts(r.get("created_at")) or ts
        if sha not in pushed or started < pushed[sha]:
            pushed[sha] = started
        if r.get("name") not in required:
            continue
        if r.get("conclusion") == "success":
            seen = green.setdefault(sha, {})
            if r["name"] not in seen or ts < seen[r["name"]]:
                seen[r["name"]] = ts
        elif r.get("conclusion") in ("failure", "cancelled", "timed_out"):
            failed.setdefault(sha, set()).add(r["name"])
    red = [pushed[sha] for sha, names in failed.items() if names - set(green.get(sha, {}))]
    cutoff = max(red) if red else None
    complete = [max(names.values()) for sha, names in green.items()
                if set(names) == required and (cutoff is None or pushed[sha] > cutoff)]
    return min(complete) if complete else None


def pr_metrics(number: int, gh_dir: Path, spawned_at: Optional[str]) -> Optional[Json]:
    pull = load_json(gh_dir / f"{number}.pull.json")
    if pull is None:
        return None
    commits = load_json(gh_dir / f"{number}.commits.json")
    reviews = load_json(gh_dir / f"{number}.reviews.json")
    runs_doc = load_json(gh_dir / f"{number}.runs.json")
    runs: Optional[List[Json]] = None
    if runs_doc is not None:
        saved = runs_doc.get("workflow_runs", [])
        # A truncated save (fewer runs than total_count) would undercount pushes
        # and move first green late, so it reads null instead.
        if runs_doc.get("total_count", len(saved)) <= len(saved):
            runs = [r for r in saved if r.get("event") == "pull_request"]
    merged_at = parse_ts(pull.get("merged_at"))
    green = first_green(runs, pull["head"]["sha"]) if runs is not None and merged_at else None
    return {
        "pr": number,
        "created_at": pull.get("created_at"),
        "merged_at": pull.get("merged_at"),
        "closed_at": pull.get("closed_at"),
        "pushes": len({r["head_sha"] for r in runs}) if runs is not None else None,
        "merges_of_main": sum(1 for c in commits if len(c.get("parents", [])) >= 2) if commits is not None else None,
        "review_rounds": len({r["commit_id"] for r in reviews if r.get("commit_id")}) if reviews is not None else None,
        "wait_green_to_merged_min": minutes(green, merged_at),
        "spawn_to_pr_min": minutes(parse_ts(spawned_at), parse_ts(pull.get("created_at"))),
    }


def run_window(flow: Json, prs: List[Json]) -> Tuple[Optional[datetime], Optional[datetime]]:
    starts = [parse_ts(u.get("spawned_at")) for u in flow.get("units", {}).values()]
    starts += [parse_ts(p["created_at"]) for p in prs]
    ends = [parse_ts(p["merged_at"]) for p in prs]
    start = parse_ts(flow.get("started_at")) or min((s for s in starts if s), default=None)
    end = parse_ts(flow.get("ended_at")) or max((e for e in ends if e), default=None)
    return start, end


def mean_wip(prs: List[Json], start: datetime, end: datetime) -> Tuple[float, int]:
    """Time-weighted mean and peak of open PRs between start and end."""
    edges: List[Tuple[datetime, int]] = []
    for p in prs:
        opened = parse_ts(p["created_at"])
        closed = parse_ts(p["merged_at"]) or parse_ts(p["closed_at"]) or end
        if opened is None:
            continue
        lo, hi = max(opened, start), min(closed, end)
        if lo < hi:
            edges += [(lo, 1), (hi, -1)]
    edges.sort(key=lambda e: (e[0], e[1]))
    span = (end - start).total_seconds()
    area, open_now, peak, last = 0.0, 0, 0, start
    for ts, delta in edges:
        area += open_now * (ts - last).total_seconds()
        open_now += delta
        peak = max(peak, open_now)
        last = ts
    return (round(area / span, 2) if span > 0 else 0.0), peak


def mean_of(prs: List[Json], key: str) -> Optional[float]:
    values = [p[key] for p in prs if p.get(key) is not None]
    return round(sum(values) / len(values), 2) if values else None


def wave_total(waves: List[Json], key: str) -> Optional[int]:
    values = [w.get(key) for w in waves]
    if not waves or any(not isinstance(v, int) for v in values):
        return None
    return sum(v for v in values if isinstance(v, int))


def ranked(counts: Dict[str, int]) -> List[Json]:
    return [{"file": f, "count": n} for f, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def files(value: Any) -> Set[str]:
    """A recorded file list as a set; anything but a list of strings reads empty."""
    return {f for f in value if isinstance(f, str)} if isinstance(value, list) else set()


def combining(flow: Json) -> Optional[Json]:
    """Parallelism lost to hot-file combining, per wave and per hot file.

    Null when the tracking file records no `waves`, so an older run reads
    unknown rather than a false 0 lost.
    """
    waves = flow.get("waves")
    if not isinstance(waves, list) or not waves:
        return None
    sequential: Dict[str, int] = {}
    combined: Dict[str, int] = {}
    per_wave: List[Json] = []
    for i, w in enumerate(waves, 1):
        width, units = w.get("dag_width"), w.get("units")
        lost = width - units if isinstance(width, int) and isinstance(units, int) else None
        in_order = files(w.get("sequential_hot_files"))
        for f in in_order:
            sequential[f] = sequential.get(f, 0) + 1
        for c in w.get("combines") or []:
            for f in files(c.get("hot_files")) - in_order:
                combined[f] = combined.get(f, 0) + 1
        per_wave.append({"wave": w.get("wave", i), "dag_width": width,
                         "units_after_combining": units, "units_lost": lost,
                         "combines": len(w.get("combines") or [])})
    return {"per_wave": per_wave, "hot_files": ranked(combined), "sequential_hot_files": ranked(sequential)}


def compute(tracking: Json, gh_dir: Path) -> Json:
    flow = tracking.get("flow", {})
    units = flow.get("units", {})
    spawned = {u["pr"]: u.get("spawned_at") for u in units.values() if u.get("pr") is not None}
    prs = [m for m in (pr_metrics(n, gh_dir, spawned.get(n)) for n in sorted(spawned)) if m is not None]
    events = flow.get("events", [])
    start, end = run_window(flow, prs)
    merged = sum(1 for p in prs if p["merged_at"])
    hours = (end - start).total_seconds() / 3600 if start and end else 0.0
    wip, peak = mean_wip(prs, start, end) if start and end and end > start else (None, None)

    red = [e for e in events if e.get("type") == "main_red"]
    red_min = sum(minutes(parse_ts(e.get("start")), parse_ts(e.get("end")) or end) or 0.0 for e in red)
    loads = [e for e in events if e.get("type") == "load" and e.get("load1") is not None]
    load_peak = max(loads, key=lambda e: e["load1"]) if loads else None
    tokens = (flow.get("tokens") or {}).get("total")
    waits = [p for p in prs if p["wait_green_to_merged_min"] is not None]
    largest = max(waits, key=lambda p: p["wait_green_to_merged_min"]) if waits else None

    combine = combining(flow)
    waves = flow.get("waves") if combine is not None else []
    width_total = wave_total(waves, "dag_width")
    units_total = wave_total(waves, "units")
    lost_total = width_total - units_total if width_total is not None and units_total is not None else None

    def count(kind: str) -> int:
        return sum(1 for e in events if e.get("type") == kind)

    return {
        "run": flow.get("run") or tracking.get("meta", {}).get("tag"),
        "window": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "per_pr": prs,
        "per_run": {
            "prs": len(prs),
            "merged": merged,
            "throughput_per_hour": round(merged / hours, 2) if hours > 0 else None,
            "mean_wip": wip,
            "peak_wip": peak,
            "mean_pushes": mean_of(prs, "pushes"),
            "mean_merges_of_main": mean_of(prs, "merges_of_main"),
            "mean_review_rounds": mean_of(prs, "review_rounds"),
            "mean_wait_green_to_merged_min": mean_of(prs, "wait_green_to_merged_min"),
            "mean_spawn_to_pr_min": mean_of(prs, "spawn_to_pr_min"),
            "semantic_conflicts": count("semantic_conflict"),
            "main_red_incidents": len(red),
            "main_red_min": round(red_min, 1),
            "recheck_failures": count("recheck_failure"),
            "contract_refreezes": count("contract_refreeze"),
            "load_peak": load_peak["load1"] if load_peak else None,
            "load_peak_at": load_peak.get("at") if load_peak else None,
            "tokens_per_merge": round(tokens / merged) if tokens is not None and merged else None,
            "dag_width": width_total,
            "units_after_combining": units_total,
            "units_lost_to_combining": lost_total,
        },
        "combining": combine,
        "largest_wait": {"pr": largest["pr"], "min": largest["wait_green_to_merged_min"]} if largest else None,
    }


PER_PR_COLUMNS = (
    ("pushes", "Pushes"),
    ("merges_of_main", "Merges of main"),
    ("review_rounds", "Review rounds"),
    ("wait_green_to_merged_min", "Green to merged (min)"),
    ("spawn_to_pr_min", "Spawn to PR (min)"),
)


def cell(value: Any) -> str:
    return "n/a" if value is None else str(value)


def change(now: Any, before: Any) -> str:
    if isinstance(now, (int, float)) and isinstance(before, (int, float)):
        return f"{now - before:+.2f}".rstrip("0").rstrip(".")
    return "n/a"


def ranking_line(label: str, rows: List[Json], before: List[Json], unit: str) -> str:
    if not rows:
        return f"{label}: none."
    prior = {r["file"]: r["count"] for r in before}
    return f"{label}: " + ", ".join(
        f"`{r['file']}` ({r['count']} {unit}, previous {cell(prior.get(r['file']))})" for r in rows[:5]) + "."


def render_combining(result: Json, previous: Optional[Json]) -> List[str]:
    combine = result.get("combining")
    if combine is None:
        return ["", "Hot-file combining: n/a (the tracking file records no waves)."]
    before = (previous or {}).get("combining") or {}
    lines = ["", "| Wave | DAG width | Units after combining | Units lost | Combines |",
             "|-----:|----------:|----------------------:|-----------:|---------:|"]
    for w in combine["per_wave"]:
        lines.append(f"| {w['wave']} | {cell(w['dag_width'])} | {cell(w['units_after_combining'])} "
                     f"| {cell(w['units_lost'])} | {w['combines']} |")
    lines += ["", ranking_line("Top combining hot files", combine["hot_files"],
                               before.get("hot_files") or [], "combines"),
              ranking_line("Sequential-merge hot files (merge order, not combining)",
                           combine["sequential_hot_files"], before.get("sequential_hot_files") or [], "waves")]
    return lines


def render_markdown(result: Json, previous: Optional[Json]) -> str:
    lines = [f"### Flow metrics: {result['run']}", ""]
    lines.append("| PR | " + " | ".join(h for _, h in PER_PR_COLUMNS) + " |")
    lines.append("|----|" + "|".join("---" for _ in PER_PR_COLUMNS) + "|")
    for p in result["per_pr"]:
        lines.append(f"| #{p['pr']} | " + " | ".join(cell(p[k]) for k, _ in PER_PR_COLUMNS) + " |")
    lines += ["", "| Metric | This run | Previous run | Change |", "|--------|---------:|-------------:|-------:|"]
    before = (previous or {}).get("per_run", {})
    for key, value in result["per_run"].items():
        lines.append(f"| {key} | {cell(value)} | {cell(before.get(key))} | {change(value, before.get(key))} |")
    lines += render_combining(result, previous)
    largest = result["largest_wait"]
    lines += ["", f"Largest wait: PR #{largest['pr']}, {largest['min']} min from first green to merged."
              if largest else "Largest wait: n/a (no merged PR with a green run)."]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tracking", type=Path, required=True)
    parser.add_argument("--github", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    result = compute(json.loads(args.tracking.read_text(encoding="utf-8")), args.github)
    if args.format == "json":
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    else:
        previous = load_json(args.previous) if args.previous else None
        sys.stdout.write(render_markdown(result, previous))
    return 0


if __name__ == "__main__":
    sys.exit(main())
