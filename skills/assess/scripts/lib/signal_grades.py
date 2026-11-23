"""The producer input class and grade of every default maturity-model signal (#783).

Data whose one ``lib`` import is the check-manifest loader, so the tally engine
(``lib/level_tally.py``) and the run context's ``level`` entry can read it without
reaching the scanners. ``SIGNAL_INPUTS`` is generated at import from each signal entry's
``evidence`` in ``checks/core/`` (#953).
``lib.level_signals`` re-exports every name and documents the classes and rulings.

The classification data `level_signals.py` documents and re-exports: `InputClass`, `Grade`,
`INPUT_GRADES`, `LISTED_GRADES`, `SIGNAL_INPUTS`, `UNMEASURED_VALUES`, `EMPTY_DENOMINATORS` (signal
to its set's count and the code when that count is 0, read by `levels.py`: `no_source_files`,
`no_manifests`) and the `DEFAULT_LEVELS` path, importing only `lib.manifest.check_manifest` (which imports only
`lib.manifest.check_validation` and `lib.manifest.manifest_whys`, neither a scanner), so `level_tally.py` and the run context's `level` entry read them
without importing the scanners (#744). Tests: `tests/test_signal_inputs.py`,
`tests/test_level_tally.py`, `tests/test_manifest_tables.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from lib.manifest.check_manifest import core_manifest, signal_inputs

DEFAULT_LEVELS = Path(__file__).resolve().parents[2] / "levels" / "default.toml"

InputClass = Literal["config_text", "head_text", "head_structure", "ci_executed",
                     "git_history", "execution_artifact", "platform"]
Grade = Literal["config_only", "declared", "measured", "verified"]

INPUT_GRADES: dict[InputClass, Grade] = {
    "config_text": "config_only",
    "head_text": "declared",
    "head_structure": "measured",
    "git_history": "measured",
    "execution_artifact": "measured",
    "ci_executed": "verified",
    "platform": "verified",
}
LISTED_GRADES: frozenset[str] = frozenset({"config_only", "declared"})

# Signal path -> its producer input class: each signal entry's `evidence` in
# checks/core/ (#953), validated against `InputClass` by the loader.
SIGNAL_INPUTS: dict[str, InputClass] = cast(
    "dict[str, InputClass]", signal_inputs(core_manifest()))


# Signal -> (the value meaning "could not be measured", the tally's reason code).
UNMEASURED_VALUES: dict[str, tuple[bool, str]] = {
    "test_pressure.mutation_run": (False, "needs_full_run"),
    "promissory_markers.aging_reliable": (False, "degenerate_history"),
}

# Signal -> (its denominator's path, the tally's reason code when that count is 0).
# A statistic over an empty set is not a measurement: a p95 of no files is 0 and
# would pass any "at most" test (#906).
EMPTY_DENOMINATORS: dict[str, tuple[str, str]] = {
    # No production source file is not a small one (#820).
    "stats_summary.loc_by_class.source.p95": (
        "stats_summary.loc_by_class.source.files_scored", "no_source_files"),
    # No manifest to lock is not a lockfile check passed (#927).
    "agent_environment.pinning.unlocked_total": ("agent_environment.pinning.manifests_total", "no_manifests"),
}
