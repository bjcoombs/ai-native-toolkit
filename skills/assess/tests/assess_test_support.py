"""Shared helpers the skills/assess tests import by name.

Import these from here, never with ``from conftest import``. When the
skills/assess tree and the root ``tests/`` tree are collected in one process,
the bare module name ``conftest`` resolves to whichever conftest was imported
first, so the import fails or picks up the wrong helpers (#1056). This
module's name is unique across every test tree in the repository;
``test_test_session_isolation.py`` keeps it that way.

Dependency stand-ins (#831). ``complexity-treemap.py`` and ``doc-graph-svg.py``
import lizard, matplotlib, numpy and squarify when they load, and the tests
load them with stand-in modules in ``sys.modules``. ``stub_heavy_deps``
installs the stand-ins through a ``MonkeyPatch``, so they go again when the
caller's scope ends. A stand-in left behind changes later tests in the same
worker: ``pytest.approx`` calls ``numpy.isscalar``, and
``lib.run.headless_layers.lizard_analyzer`` reads a lizard stand-in as "lizard
missing", which empties the ``stats_summary`` block (#1108). The conftest
guard uses ``stand_in_snapshot`` and ``leaked_stand_ins`` to fail any test
module that leaves one behind.

A module imported while the stand-ins are in place, such as
``lib.complexity.treemap_render``, stays cached in ``sys.modules`` with its
``np`` still bound to the stub after the scope ends. That is harmless: the tests
that call into it hold the stand-ins while they do.

Toolkit-root tests (#1649). A test that reads the ai-native-toolkit repository
itself (workflows, ``action.yml``, root docs, the plugin manifest) reaches it
through ``TOOLKIT_ROOT`` and carries the ``toolkit_repo`` marker. The root is
confirmed by ``.claude-plugin/plugin.json`` naming this plugin, so in a vendored
copy of the skill tree, where that directory is another repository, the conftest
skips the marked tests with a reason (``gate_toolkit_repo_tests``). It is a root
probe, not a parking marker: in this repository's CI (``GITHUB_REPOSITORY``
names it) a missing root fails the session, so the tests never skip there.
"""
from __future__ import annotations

import datetime as _dt
import importlib
import json
import sys
import types
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal, Protocol

import pytest

# Windows skip markers (#713). The windows-latest job runs this suite; a test
# that needs POSIX-only machinery carries one of these markers and is skipped
# there with the marker's reason, so the skip list reads as a category, not a
# scatter of ad-hoc skipif lines. Anything else that fails on Windows is a bug.
WINDOWS_SKIP_MARKERS = {
    "needs_symlink": "symlink creation is not reliable on Windows",
    "needs_posix_perms": "chmod/getuid permission semantics are POSIX-only",
    "needs_posix_shell": "runs a #!/bin/sh stub or sh -c, which Windows cannot execute",
}


# The toolkit root (#1649): the directory that holds skills/, confirmed by its plugin manifest.
TOOLKIT_PLUGIN_NAME = "ai-native-toolkit"
TOOLKIT_REPOSITORY = "bjcoombs/ai-native-toolkit"
TOOLKIT_REPO_MARKER = "toolkit_repo"
TOOLKIT_ROOT = Path(__file__).resolve().parents[3]


def is_toolkit_root(root: Path) -> bool:
    """Whether ``root/.claude-plugin/plugin.json`` names this plugin."""
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == TOOLKIT_PLUGIN_NAME


TOOLKIT_ROOT_PRESENT = is_toolkit_root(TOOLKIT_ROOT)


def toolkit_root_verdict(present: bool, github_repository: str | None) -> Literal["run", "skip", "fail"]:
    """Run the marked tests at the root; skip them elsewhere, except in this repository's CI."""
    if present:
        return "run"
    if (github_repository or "").lower() == TOOLKIT_REPOSITORY:
        return "fail"
    return "skip"


class _MarkableItem(Protocol):
    def get_closest_marker(self, name: str) -> object | None: ...
    def add_marker(self, marker: pytest.MarkDecorator) -> None: ...


def gate_toolkit_repo_tests(items: Iterable[_MarkableItem], *, root: Path, present: bool,
                            github_repository: str | None) -> None:
    """Skip the ``toolkit_repo`` tests outside the toolkit root, or fail the session in its CI."""
    verdict = toolkit_root_verdict(present, github_repository)
    if verdict == "run":
        return
    where = root.as_posix()
    if verdict == "fail":
        raise pytest.UsageError(
            f"GITHUB_REPOSITORY is {github_repository}, but {where} is not the {TOOLKIT_PLUGIN_NAME} "
            "root (no .claude-plugin/plugin.json naming it), so the toolkit_repo tests would skip (#1649)")
    reason = f"reads the {TOOLKIT_PLUGIN_NAME} repository, and {where} is not its root (#1649)"
    for item in items:
        if item.get_closest_marker(TOOLKIT_REPO_MARKER):
            item.add_marker(pytest.mark.skip(reason=reason))


def skip_without_posix_shell() -> None:
    """Call first in a fixture that puts a ``#!/bin/sh`` stub (fake gh/glab) on PATH.

    The fixture-level form of ``needs_posix_shell``: Windows cannot execute the
    stub, so every test using the fixture skips with the same reason.
    """
    if sys.platform == "win32":
        pytest.skip(f"Windows: {WINDOWS_SKIP_MARKERS['needs_posix_shell']}")


def pin_fleet_run_date(monkeypatch: pytest.MonkeyPatch, cli: object) -> _dt.date:
    """Pin ``cli.utc_today`` (``skills/fleet/scripts/fleet.py``) to the date it reads now."""
    day: _dt.date = cli.utc_today()  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "utc_today", lambda: day)
    return day


def use_real_lizard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the installed lizard in ``sys.modules`` until ``monkeypatch`` undoes it.

    The undo restores what stood there before: a stand-in, or no entry at all. An
    installed lizard already in place is left alone.

    ``lib.run.headless_layers.lizard_analyzer`` reads a stand-in as "lizard not
    installed", so a headless ``stats_summary`` built while one is in place reads
    ``no_lizard`` (#1117). ``stub_heavy_deps`` now removes its stand-ins when the
    loading fixture's scope ends (#831); this keeps a headless scan scored even
    inside a module that still holds one.
    """
    module = sys.modules.get("lizard")
    if module is not None and hasattr(module, "analyze_file"):
        return
    monkeypatch.setitem(sys.modules, "lizard", module)  # records the stand-in, or no entry
    del sys.modules["lizard"]
    importlib.import_module("lizard")


# The modules the CLI wrappers import at load and the tests stand in for.
HEAVY_DEPS = ("lizard", "matplotlib", "matplotlib.pyplot", "numpy", "squarify")


class StubNumpy(types.ModuleType):
    """Minimal numpy: only percentile, with numpy's default linear interp."""

    @staticmethod
    def percentile(values: list[float], q: float) -> float:
        s = sorted(values)
        if not s:
            return 0.0
        k = (len(s) - 1) * q / 100.0
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return float(s[f] + (s[c] - s[f]) * (k - f))


def stub_heavy_deps(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...] = HEAVY_DEPS) -> None:
    """Stand in for each of ``names`` that is not imported, until ``monkeypatch`` undoes it.

    numpy gets a ``StubNumpy`` unless the one in ``sys.modules`` has ``percentile``.
    """
    for name in names:
        if name == "numpy":
            if not hasattr(sys.modules.get("numpy"), "percentile"):
                monkeypatch.setitem(sys.modules, "numpy", StubNumpy("numpy"))
        elif name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    if "matplotlib.pyplot" in names:
        # The wrappers do `import matplotlib.pyplot as plt`.
        monkeypatch.setattr(sys.modules["matplotlib"], "pyplot", sys.modules["matplotlib.pyplot"],
                            raising=False)


def stand_in_snapshot() -> dict[str, object | None]:
    """What ``sys.modules`` holds for each of ``HEAVY_DEPS`` now."""
    return {name: sys.modules.get(name) for name in HEAVY_DEPS}


def leaked_stand_ins(before: Mapping[str, object | None], after: Mapping[str, object | None]) -> list[str]:
    """The names that hold a stand-in in ``after`` that they did not hold in ``before``.

    A stand-in is a module with no ``__spec__``: a real import always has one. A
    name that is newly imported for real, or whose stand-in was removed, is fine.
    """
    return sorted(
        name for name, module in after.items()
        if module is not None and module is not before.get(name)
        and getattr(module, "__spec__", None) is None
    )
