"""Practice packs across a fleet: one model for every repository and a roll-up per pack (#1276).

Part E of #1266. ``fleet.py --pack FILE`` (repeatable) applies the same practice packs
(``lib/packs.py``) to every repository of a run and reports per pack.

**Loading** (``load_fleet_packs``), before any repository is scanned: each pack is read once
(``packs.read_packs``, which refuses two packs of one name), the ``--levels`` or ``--profile``
model is loaded with the packs' signal paths allowed (``level_signals.signal_path_validator``)
and each pack's ``[[rows]]`` are layered onto it in pass order (``pack_layering.layer_packs``),
so every row names its ``source``. Any fault raises ``PackError`` or ``LevelsError``, which
``fleet.py`` turns into exit 2 before a scan. ``--pack`` needs a model (``fleet_cli_args``).

**Same packs everywhere** (``pack_copies``): the text each pack was parsed from is written to
a private directory the run owns, and every repository's ``assess_signals.py --pack`` reads
those copies in pass order, so an edit to a pack file mid-run reaches no repository. Each row
carries the ``pack`` block counts-only (booleans, counts, unavailable markers; no path or file
text; ``signals.PACK_BLOCKS``). The pack identities are the run stamp's ``packs`` alone.

**Roll-up** (``pack_roll_up``): for each pack, in pass order, its ``name``, ``version`` and
``sha256`` and each practice it adds: every condition of a row whose ``source`` is the pack, in
model order, with ``met`` (the repositories where it holds: met, or unmet for a hazard),
``measured`` (met or unmet) and ``not_measured`` (a check unavailable, a signal absent, or a
``requires`` not measured), each over ``repos``, the scanned repositories. A repository where
a pack check could not be answered is not measured, never unmet; one whose own
``.assess/config.toml`` excludes the condition (``not_applicable``, #910) is in neither column,
so ``measured + not_measured`` can fall short of ``repos``. The practices come from the model,
so a pack's rows are listed at zero counts when no repository scanned. A pack with checks but no
rows lists no practice. ``level.json`` (``fleet_level``) and ``fleet-data.json``
(``fleet_data``) carry it as ``packs``, written only when a pack was passed, so a run without
``--pack`` writes the files it wrote before; ``report.md`` gets ``packs_section``, and
``fleet-view.html`` a Practice packs section (``fleet_page_data``).

Every string is the pack author's (pack name, version, row id and title) or a hash; none
comes from a repository, so counts-only output keeps them, and ``--hash-paths`` keeps the
pack name readable: it names the operator's pack, not a repository or unit.
Tests: ``tests/test_fleet_packs.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from lib.level_result_types import LevelResult
from lib.level_signals import signal_path_validator
from lib.levels import MaturityModel, condition_id, load_levels
from lib.pack_layering import layer_packs
from lib.packs import Pack, PackIdentity, pack_identity, read_packs

PACKS_KEY = "packs"
PACKS_DIR = "packs"


class PackPractice(TypedDict):
    condition: str  # <row id>.<level>
    row: str
    title: str
    level: int
    met: int  # repositories where it holds
    measured: int  # repositories where it was met or unmet
    not_measured: int  # scanned repositories where it could not be read


class PackRollUp(TypedDict):
    name: str
    version: str
    sha256: str
    repos: int  # scanned repositories, the denominator of every practice
    practices: list[PackPractice]


class FleetPacks(NamedTuple):
    """The run's packs, in pass order, each with the text it was parsed from."""

    packs: tuple[Pack, ...] = ()
    texts: tuple[str, ...] = ()

    def identities(self) -> list[PackIdentity]:
        return [pack_identity(pack) for pack in self.packs]


def load_fleet_packs(paths: Sequence[str]) -> FleetPacks:
    """Every ``--pack`` file, read once; ``PackError`` on a fault."""
    loaded = read_packs(paths)
    return FleetPacks(tuple(p for p, _ in loaded), tuple(t for _, t in loaded))


def fleet_model(levels: Path, base_source: str, packs: Sequence[Pack]) -> MaturityModel:
    """The ``--levels`` model with each pack's rows layered on, every pack path allowed;
    ``LevelsError`` or ``PackError`` on a fault. Without packs it is ``load_levels`` alone."""
    validator = signal_path_validator(packs)
    return layer_packs(load_levels(levels, validator), base_source, packs, validator)


def pack_copies(directory: Path, fleet_packs: FleetPacks) -> tuple[Path, ...]:
    """Write each pack's text under ``directory/packs/<n>/<name>.pack.toml``; the paths in
    pass order, which every repository's scan reads."""
    paths: list[Path] = []
    for index, (pack, text) in enumerate(zip(fleet_packs.packs, fleet_packs.texts), 1):
        path = directory / PACKS_DIR / str(index) / f"{pack['name']}.pack.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        paths.append(path.resolve())
    return tuple(paths)


def _holds(condition: Mapping[str, Any]) -> bool:
    return bool(condition["state"] == ("unmet" if condition.get("polarity") == "hazard" else "met"))


def pack_roll_up(model: MaturityModel | None, identities: Sequence[Mapping[str, Any]],
                 results: Iterable[LevelResult | None]) -> list[PackRollUp]:
    """Each pack's practices over the scanned repositories' level results (None: not scanned);
    ``[]`` without a pack or a model."""
    if model is None or not identities:
        return []
    scanned = [r for r in results if r is not None]
    out: list[PackRollUp] = []
    for ident in identities:
        name = str(ident.get("name"))
        # Seeded from the model, so a pack's practices are listed even when no repository scanned.
        practices: dict[str, PackPractice] = {
            condition_id(row["id"], lvl["level"]): {
                "condition": condition_id(row["id"], lvl["level"]), "row": row["id"], "title": row["title"],
                "level": lvl["level"], "met": 0, "measured": 0, "not_measured": 0}
            for row in model["rows"] if row.get("source") == name for lvl in row["levels"]}
        for result in scanned:
            for c in result["conditions"]:
                p = practices.get(c["id"]) if c.get("source") == name else None
                if p is None:
                    continue
                p["met"] += _holds(c)
                p["measured"] += c["state"] in ("met", "unmet")
                p["not_measured"] += c["state"] == "not_measured"
        out.append({"name": name, "version": str(ident.get("version")), "sha256": str(ident.get("sha256")),
                    "repos": len(scanned), "practices": list(practices.values())})
    return out


def _practice_words(p: Mapping[str, Any]) -> str:
    title = str(p["title"]) if p["level"] == 1 else f"{p['title']}, level {p['level']}"
    return title.replace("|", "/")


def packs_section(roll_up: Sequence[Mapping[str, Any]] | None) -> str | None:
    """``report.md``'s "Practice packs" section; None when the run had no pack."""
    if not roll_up:
        return None
    count = sum(len(pack["practices"]) for pack in roll_up)
    repos = roll_up[0]["repos"]
    lines = ["## Practice packs", "",
             f"In place: {count} practice{'s' if count != 1 else ''} from {len(roll_up)} "
             f"pack{'s' if len(roll_up) != 1 else ''}, read over {repos} scanned "
             f"repositor{'ies' if repos != 1 else 'y'}.", "",
             "Each pack applied to every repository. A repository where a pack check could not be "
             "read counts as not measured, never as not met.", ""]
    for pack in roll_up:
        lines += [f"### Pack {pack['name']} (version {pack['version']})", ""]
        if not pack["practices"]:
            lines += ["This pack adds checks only, no practice to score.", ""]
            continue
        lines += ["| Practice | Repositories that meet it | Measured | Not measured |", "|---|---|---|---|"]
        lines += [f"| {_practice_words(p)} | {p['met']} | {p['measured']} | {p['not_measured']} |"
                  for p in pack["practices"]]
        lines.append("")
    return "\n".join(lines)
