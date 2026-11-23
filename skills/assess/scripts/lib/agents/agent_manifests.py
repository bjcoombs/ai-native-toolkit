"""Dependency names declared in a repository's manifests (issue #689).

The ``agent_building`` scan asks one question of a manifest: which
dependencies does it declare? Each reader below returns the declared names of
one manifest format, read with the standard library only. A file a reader
cannot parse yields no names rather than an error, so one malformed manifest
never hides the evidence in the others.

Formats: ``package.json`` (dependency maps), ``pyproject.toml`` (PEP 621
dependencies and optional dependencies, dependency groups, Poetry
dependencies), ``requirements*.txt``, ``Pipfile``, ``go.mod``, ``pom.xml``,
``build.gradle`` / ``build.gradle.kts``, ``Cargo.toml``, ``Gemfile`` and
``*.csproj``. Names are returned as written; ``agent_building_vocab.lookup``
normalises them.

Dependency names declared in one manifest file (issue #689), stdlib only: one reader
per format (`package_json`, `pyproject`, `requirements`, `pipfile`, `go_mod`,
`pom_xml`, `gradle`, `cargo_toml`, `gemfile`, `csproj`), and `reader_for(basename)`
picking one. A file a reader cannot parse yields no names. Tests:
`tests/test_agent_building.py`.
Tests: `tests/test_shared_tooling.py`.
"""
from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable
from typing import Any

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([a-z0-9.-]+\.[a-z]+/[^\s]+)\s+v[0-9]", re.MULTILINE)
_POM_DEP = re.compile(r"<dependency>(.*?)</dependency>", re.DOTALL)
_POM_GROUP = re.compile(r"<groupId>\s*([^<\s]+)\s*</groupId>")
_POM_ARTIFACT = re.compile(r"<artifactId>\s*([^<\s]+)\s*</artifactId>")
_GRADLE_COORD = re.compile(r"""["']([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)(?::[^"']*)?["']""")
_GEM = re.compile(r"""^\s*gem\s+["']([^"']+)["']""", re.MULTILINE)
_CSPROJ = re.compile(r"""<PackageReference\s+Include\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _requirement_names(lines: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for line in lines:
        if not isinstance(line, str) or line.lstrip().startswith(("#", "-")):
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.append(match.group(1))
    return names


def _toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _table(data: Any, *keys: str) -> Any:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _keys(data: Any) -> list[str]:
    return [k for k in data if isinstance(k, str)] if isinstance(data, dict) else []


def package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return [name for section in _NPM_SECTIONS for name in _keys(_table(data, section))]


def _list(data: Any) -> list[Any]:
    """``data`` when it is a list, else empty: a string is never read per character."""
    return data if isinstance(data, list) else []


def _tables(data: Any) -> list[Any]:
    """The values of ``data`` when it is a table, else empty."""
    return list(data.values()) if isinstance(data, dict) else []


def pyproject(text: str) -> list[str]:
    data = _toml(text)
    lines: list[Any] = list(_list(_table(data, "project", "dependencies")))
    for group in _tables(_table(data, "project", "optional-dependencies")):
        lines += _list(group)
    for group in _tables(_table(data, "dependency-groups")):
        lines += _list(group)
    lines += _list(_table(data, "tool", "uv", "dev-dependencies"))
    names = _requirement_names(lines)
    poetry = _table(data, "tool", "poetry")
    names += [k for k in _keys(_table(poetry, "dependencies")) if k.lower() != "python"]
    names += _keys(_table(poetry, "dev-dependencies"))
    for group in _tables(_table(poetry, "group")):
        names += _keys(_table(group, "dependencies"))
    return names


def requirements(text: str) -> list[str]:
    return _requirement_names(text.splitlines())


def pipfile(text: str) -> list[str]:
    data = _toml(text)
    return _keys(data.get("packages")) + _keys(data.get("dev-packages"))


def go_mod(text: str) -> list[str]:
    return _GO_REQUIRE.findall(text)


def pom_xml(text: str) -> list[str]:
    names: list[str] = []
    for block in _POM_DEP.findall(text):
        group, artifact = _POM_GROUP.search(block), _POM_ARTIFACT.search(block)
        if group and artifact:
            names.append(f"{group.group(1)}:{artifact.group(1)}")
    return names


def gradle(text: str) -> list[str]:
    return [f"{group}:{artifact}" for group, artifact in _GRADLE_COORD.findall(text)]


def cargo_toml(text: str) -> list[str]:
    data = _toml(text)
    return _keys(data.get("dependencies")) + _keys(data.get("dev-dependencies"))


def gemfile(text: str) -> list[str]:
    return _GEM.findall(text)


def csproj(text: str) -> list[str]:
    return _CSPROJ.findall(text)


_BY_NAME: dict[str, Callable[[str], list[str]]] = {
    "package.json": package_json,
    "pyproject.toml": pyproject,
    "pipfile": pipfile,
    "go.mod": go_mod,
    "pom.xml": pom_xml,
    "build.gradle": gradle,
    "build.gradle.kts": gradle,
    "cargo.toml": cargo_toml,
    "gemfile": gemfile,
}


def reader_for(basename: str) -> Callable[[str], list[str]] | None:
    """The reader for a manifest file name, or None when it is not a manifest."""
    name = basename.lower()
    if name in _BY_NAME:
        return _BY_NAME[name]
    if name.startswith("requirements") and name.endswith(".txt"):
        return requirements
    if name.endswith(".csproj"):
        return csproj
    return None
