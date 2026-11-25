"""Each condition's estimated effect on agent outcomes across the fleet run history (#1086).

The exit rule asks whether a condition moves the agent-outcome measure (``lib.agent_outcome``,
#1085): do repositories that meet it see fewer agent commits reverted or reworked? This
module answers with an estimate, its interval and its sample size, and changes nothing:
no Step, count or headline reads it. The demotion that acts on it is a separate step.

**Observations.** The runs are this run (from memory) and the comparable earlier entries
of the run history (``history_runs``: one per scan date, ``fleet_history.one_per_date``,
stamps matching on ``fleet_trends.MATCH_KEYS``: window, scans, output mode, path hashing,
``--fleet-config`` and ``--platform``), less a hashed entry that shares no repository id with
this run: ``--hash-paths`` without a fixed salt hashes each run under a new one, so its ids
would count every repository once per run (the achievements rule, #909). Every run is re-scored under this run's maturity
model (``fleet_level.repo_level``), so a condition reads the same criteria in every run.
For each condition and each measure in ``MEASURES``, a repository gives one observation:
its newest run in which the condition is ``met`` or ``unmet`` and the share is a number
(``agent_outcome`` available, share not null). One per repository, not one per run,
because consecutive runs read overlapping windows of the same commits, so two runs of one
repository are not independent samples. A failed row, a not-measured or not-applicable
state, an unavailable ``agent_outcome`` block and a null share give no observation, so an
unknown never reads as an outcome of 0.

**Estimate.** ``effect`` is the mean share of the repositories that meet the condition
minus the mean of those that do not: negative when meeting it goes with fewer agent
commits reverted or reworked. ``interval`` is Welch's two-sample interval at
``CONFIDENCE`` (unequal variances, Welch-Satterthwaite degrees of freedom, the Student t
quantile from the regularized incomplete beta function, so no statistics package is
needed). ``includes_zero`` says whether the interval spans 0: no effect detected. With
fewer than two observations on a side there is no variance and no interval
(``reason: "too_few_repositories"``); with every share equal on both sides there is no
interval either (``reason: "no_spread"``), since a point would read as certainty, and an
all-zero fleet says nothing about an effect. ``low_confidence`` is true whenever either side
holds fewer than ``MIN_PER_SIDE`` repositories. The exit rule reads only results with
``low_confidence`` false.

**Document** (``condition_effect.json`` in ``fleet.py --out``, with ``--levels``)::

    {"condition_effect_version": 1, "levels_version": str, "runs": [scan_date, ...],
     "repositories": int, "min_per_side": int, "confidence": float, "measures": [...],
     "conditions": [{"id": str, "predicted": str | None, "review": str | None,
                     "effects": [{"measure": str, "met": int, "unmet": int,
                                  "mean_met": float | None, "mean_unmet": float | None,
                                  "effect": float | None, "interval": [low, high] | None,
                                  "includes_zero": bool | None, "low_confidence": bool,
                                  "reason": str | None}]}]}

Counts, shares, dates and condition ids only: no repository id or path. Numbers are
rounded to ``DIGITS`` places, conditions are in model order, and nothing reads the clock,
so the same history gives the same bytes.

Each condition's outcome effect over the fleet history (#1086), informational.
`history_runs(org_dir, current, repos)` reads the comparable earlier entries' `repos.jsonl`,
passing over a hashed entry under another salt (`_same_salt`);
`observations(runs, model)` keeps each repository's newest met-or-unmet state with a numeric
share per condition and measure (`MEASURES`: `revert_share`, `rework_share`); `welch(met,
unmet)` gives the mean difference and its Welch interval at `CONFIDENCE` (0.95), with
`t_quantile` from the regularized incomplete beta; `low_confidence` under `MIN_PER_SIDE`
(10) a side, `too_few_repositories` and no interval under 2, `no_spread` and no interval when no share varies. `build_condition_effect` writes
the document; `write_condition_effect` saves `condition_effect.json`.
Tests: `tests/test_condition_effect.py`.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from lib.counts_only.content_free import is_hashed
from lib.fleet.fleet_history import one_per_date
from lib.fleet.fleet_legacy import stamp_value, upgrade
from lib.fleet.fleet_level import repo_level
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp
from lib.fleet.fleet_trends import MATCH_KEYS
from lib.fleet.fleet_view import read_repos
from lib.fleet.runner import STATUS_OK, FleetRow
from lib.scoring.levels import MaturityModel

CONDITION_EFFECT_FILE = "condition_effect.json"
CONDITION_EFFECT_VERSION = 1
# The agent_outcome shares an effect is estimated on (lib/agent_outcome.py, #1085).
MEASURES = ("revert_share", "rework_share")
# Repositories needed on each side before a result is trusted: a common rule of thumb for a
# two-sample comparison of means, chosen by the run 4 lead on #1086 and open to the maintainer's veto.
MIN_PER_SIDE = 10
# The interval's coverage: the conventional 95% two-sided level (alpha = 0.05).
CONFIDENCE = 0.95
TOO_FEW = "too_few_repositories"
# Every share on both sides is equal: no variance, so no interval and no detected effect either way.
NO_SPREAD = "no_spread"
DIGITS = 6
_SCORED = {"met": True, "unmet": False}


class MeasureEffect(TypedDict):
    measure: str
    met: int  # repositories observed meeting the condition
    unmet: int
    mean_met: float | None
    mean_unmet: float | None
    effect: float | None  # mean_met - mean_unmet
    interval: list[float] | None  # [low, high] at CONFIDENCE
    includes_zero: bool | None
    low_confidence: bool
    reason: str | None


class ConditionEffect(TypedDict):
    id: str
    predicted: str | None
    review: str | None
    effects: list[MeasureEffect]


class ConditionEffectDoc(TypedDict):
    condition_effect_version: int
    levels_version: str
    runs: list[str]
    repositories: int
    min_per_side: int
    confidence: float
    measures: list[str]
    conditions: list[ConditionEffect]


class HistoryRun(NamedTuple):
    scan_date: str
    rows: list[FleetRow]


# --- the Student t quantile ------------------------------------------------------------


def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function (modified Lentz)."""
    tiny, c, d = 1e-300, 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        for num in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                    -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """The Student t distribution function at ``t`` with ``df`` degrees of freedom."""
    tail = 0.5 * _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: float) -> float:
    """The ``p`` quantile (0.5 < p < 1) of Student t with ``df`` degrees of freedom, by bisection."""
    low, high = 0.0, 1.0
    while t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# --- the estimate ----------------------------------------------------------------------


def _mean_var(values: Sequence[float]) -> tuple[float, float]:
    mean = math.fsum(values) / len(values)
    return mean, math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)


def _round(value: float) -> float:
    return round(value, DIGITS) + 0.0  # + 0.0 turns -0.0 into 0.0


def welch(measure: str, met: Sequence[float], unmet: Sequence[float]) -> MeasureEffect:
    """The mean difference ``met - unmet`` and its Welch interval at ``CONFIDENCE``."""
    effect = MeasureEffect(measure=measure, met=len(met), unmet=len(unmet),
                           mean_met=_round(math.fsum(met) / len(met)) if met else None,
                           mean_unmet=_round(math.fsum(unmet) / len(unmet)) if unmet else None,
                           effect=None, interval=None, includes_zero=None,
                           low_confidence=min(len(met), len(unmet)) < MIN_PER_SIDE, reason=None)
    if min(len(met), len(unmet)) < 2:
        effect["reason"] = TOO_FEW
        return effect
    (m1, v1), (m2, v2) = _mean_var(met), _mean_var(unmet)
    diff = m1 - m2
    s1, s2 = v1 / len(met), v2 / len(unmet)
    if len(set(met)) == 1 and len(set(unmet)) == 1:  # a point "interval" would read as certainty
        effect["effect"], effect["reason"] = _round(diff), NO_SPREAD
        return effect
    se = math.sqrt(s1 + s2)
    df = (s1 + s2) ** 2 / (s1 ** 2 / (len(met) - 1) + s2 ** 2 / (len(unmet) - 1))
    half = t_quantile(1.0 - (1.0 - CONFIDENCE) / 2.0, df) * se
    low, high = _round(diff - half), _round(diff + half)
    effect["effect"], effect["interval"], effect["includes_zero"] = _round(diff), [low, high], low <= 0.0 <= high
    return effect


# --- the observations ------------------------------------------------------------------


def _shares(row: FleetRow) -> dict[str, float] | None:
    block = (row.get("scans") or {}).get("agent_outcome")
    if not isinstance(block, Mapping) or block.get("available") is False:
        return None
    return {m: float(v) for m in MEASURES
            if isinstance(v := block.get(m), (int, float)) and not isinstance(v, bool) and math.isfinite(v)}


Observed = dict[tuple[str, str], dict[str, tuple[bool, float]]]


def observations(runs: Sequence[HistoryRun], model: MaturityModel) -> tuple[Observed, int]:
    """Per (condition id, measure), each repository's newest ``(met, share)``; and the repository count.

    ``runs`` are oldest first, so a later run's observation replaces an earlier one."""
    seen: Observed = {}
    repos: set[str] = set()
    for run in runs:
        for row in run.rows:
            if row.get("status") != STATUS_OK or (shares := _shares(row)) is None or not shares:
                continue
            for condition in repo_level(model, row)["conditions"]:
                met = _SCORED.get(condition["state"])
                if met is None:
                    continue
                for measure, share in shares.items():
                    seen.setdefault((condition["id"], measure), {})[row["repo"]] = (met, share)
                    repos.add(row["repo"])
    return seen, len(repos)


def build_condition_effect(model: MaturityModel, runs: Sequence[HistoryRun]) -> ConditionEffectDoc:
    """The document over ``runs`` (oldest first, this run last), re-scored under ``model``."""
    seen, repositories = observations(runs, model)
    conditions: list[ConditionEffect] = []
    for row in model["rows"]:
        for level in row["levels"]:
            cid = f"{row['id']}.{level['level']}"
            effects = []
            for measure in MEASURES:
                values = seen.get((cid, measure), {})
                effects.append(welch(measure, [s for _r, (m, s) in sorted(values.items()) if m],
                                     [s for _r, (m, s) in sorted(values.items()) if not m]))
            conditions.append(ConditionEffect(id=cid, predicted=level.get("predicted"),
                                              review=level.get("review"), effects=effects))
    return ConditionEffectDoc(
        condition_effect_version=CONDITION_EFFECT_VERSION, levels_version=model["version"],
        runs=[run.scan_date for run in runs], repositories=repositories, min_per_side=MIN_PER_SIDE,
        confidence=CONFIDENCE, measures=list(MEASURES), conditions=conditions)


# --- the history -----------------------------------------------------------------------


def _comparable(stamp: Any, current: FleetStamp) -> bool:
    return (isinstance(stamp, dict) and stamp.get("fleet_stamp_version") == FLEET_STAMP_VERSION
            and all(stamp_value(stamp, key) == stamp_value(current, key) for key in MATCH_KEYS))


def _read_entry(entry: Path, date: str, current: FleetStamp) -> HistoryRun | None:
    """The entry's rows; None when it is unreadable, not comparable or dated otherwise."""
    try:
        stamp: Any = json.loads((entry / RUN_FILE).read_text(encoding="utf-8"))
        if isinstance(stamp, dict):
            stamp = upgrade(stamp)  # an entry written before the 2.0.0 rename
        if not _comparable(stamp, current) or stamp.get("scan_date") != date:
            return None
        return HistoryRun(date, read_repos(entry))
    except (OSError, ValueError, KeyError, TypeError):  # FleetViewError is a ValueError
        return None


def _same_salt(run: HistoryRun, repos: frozenset[str]) -> bool:
    """False for a hashed entry that shares no repository id with this run: it was hashed under
    another salt, so its ids would count each repository again (the achievements rule, #909)."""
    ids = {row["repo"] for row in run.rows}
    return not any(is_hashed(r) for r in ids) or bool(ids & repos)


def history_runs(org_dir: Path, current: FleetStamp, repos: frozenset[str],
                 read: Callable[[Path, str, FleetStamp], HistoryRun | None] = _read_entry) -> list[HistoryRun]:
    """The comparable earlier runs under ``org_dir``, one per scan date before this run's, oldest
    first; a hashed entry under another salt is passed over (``_same_salt``)."""
    def keep(entry: Path, date: str) -> HistoryRun | None:
        run = read(entry, date, current)
        return run if run is not None and _same_salt(run, repos) else None
    return [run for _date, run in one_per_date(org_dir, current["scan_date"], repos, keep)]


def write_condition_effect(out_dir: Path, doc: ConditionEffectDoc) -> Path:
    path = out_dir / CONDITION_EFFECT_FILE
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
                    newline="\n")
    return path
