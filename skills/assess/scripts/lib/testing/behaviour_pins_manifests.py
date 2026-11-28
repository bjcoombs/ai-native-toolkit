"""The dependency and signature readers behind ``lib.testing.behaviour_pins`` (#855).

Each reader takes one file's text and returns the arms it declares, as ``(practice, arm)``
pairs. A match is always a parsed declaration, never a substring of the text (the first #826
probe's bare ``insta`` matched ``install`` in every repository):

- **Python** (``python_names``): requirement names parsed from ``pyproject.toml`` with
  ``tomllib`` (``project.dependencies``, ``project.optional-dependencies``,
  ``dependency-groups``, the Poetry, PDM, uv and Hatch dependency tables), from a
  ``setup.cfg`` with ``configparser`` (``install_requires``, ``tests_require``,
  ``extras_require``) and from ``requirements*.txt`` lines. A name is the PEP 508 name at
  the start of a requirement, normalised (``pytest_socket`` reads as ``pytest-socket``).
- **Rust** (``cargo_names``): the keys of a ``Cargo.toml`` dependency table
  (``dependencies``, ``dev-dependencies``, ``build-dependencies``, ``workspace.dependencies``
  and their ``target.<cfg>`` forms), a renamed one by its ``package``.
- **JavaScript** (``npm_names``): the keys of ``package.json`` ``dependencies``,
  ``devDependencies``, ``optionalDependencies`` and ``peerDependencies``; an ``@fast-check/``
  package reads as ``fast-check``.
- **JVM** (``maven_groups``, ``gradle_groups``): the ``groupId`` of a ``pom.xml``
  ``<dependency>`` (parsed with ``xml.etree``), and in a Gradle build file a quoted
  ``group:artifact`` coordinate or a ``group:`` map entry outside comments; in a version
  catalog (``*.versions.toml``) a library's ``module`` or ``group``.
- **Go** (``go_fuzz_tests``): a ``func FuzzXxx(f *testing.F)`` declaration at the start of a
  line of a ``_test.go`` file, with block comments and raw strings blanked first.

``ARMS`` maps each name to its practice. Nothing is executed or imported. Tests:
``tests/test_behaviour_pins_evidence.py``.
"""
from __future__ import annotations

import configparser
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

SCHEDULED_FUZZING = "scheduled_fuzzing"
FUZZ_TARGETS = "fuzz_targets"
PROPERTY_TESTS = "property_tests"
NETWORK_ISOLATED = "network_isolated_tests"
SNAPSHOT_TESTS = "snapshot_tests"

# Python distribution, Rust crate, npm package and JVM group names, each with its practice.
PYTHON_ARMS = {"hypothesis": PROPERTY_TESTS, "pytest-socket": NETWORK_ISOLATED, "vcrpy": NETWORK_ISOLATED,
               "pytest-recording": NETWORK_ISOLATED, "syrupy": SNAPSHOT_TESTS}
CARGO_ARMS = {"insta": SNAPSHOT_TESTS, "proptest": PROPERTY_TESTS, "quickcheck": PROPERTY_TESTS,
              "libfuzzer-sys": FUZZ_TARGETS, "arbitrary": FUZZ_TARGETS}
NPM_ARMS = {"fast-check": PROPERTY_TESTS}
JVM_GROUPS = {"net.jqwik": ("jqwik", PROPERTY_TESTS), "com.approvaltests": ("approvaltests", SNAPSHOT_TESTS)}
GO_ARM = "go-fuzz-test"

_PEP508 = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_CARGO_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
_NPM_TABLES = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_GRADLE_COORD = re.compile(r"""["']([A-Za-z0-9_.-]+):[A-Za-z0-9_.-]+""")
_GRADLE_GROUP = re.compile(r"""\bgroup\s*[:=]\s*["']([A-Za-z0-9_.-]+)["']""")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
_RAW_STRING = re.compile(r"`[^`]*`", re.S)
_GO_FUZZ = re.compile(r"^func\s+Fuzz(?:[A-Z0-9_]\w*)?\s*\(\s*\w+\s+\*testing\.F\s*\)", re.M)


def normalise(name: str) -> str:
    """A PEP 503 normalised distribution name: lower case, runs of ``-_.`` as one ``-``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    match = _PEP508.match(text)
    return normalise(match.group(1)) if match else None


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, list):
        yield from (v for v in value if isinstance(v, str))


def _table(doc: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    node: object = doc
    for key in keys:
        node = node.get(key) if isinstance(node, Mapping) else None
    return node if isinstance(node, Mapping) else {}


def _pyproject(text: str) -> Iterator[str]:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    project = _table(doc, "project")
    yield from _strings(project.get("dependencies"))
    for group in (*_table(project, "optional-dependencies").values(), *_table(doc, "dependency-groups").values(),
                  *_table(doc, "tool", "pdm", "dev-dependencies").values()):
        yield from _strings(group)
    yield from _strings(_table(doc, "tool", "uv").get("dev-dependencies"))
    poetry = _table(doc, "tool", "poetry")
    yield from _table(poetry, "dependencies")
    yield from _table(poetry, "dev-dependencies")
    for group in _table(poetry, "group").values():
        yield from _table(group, "dependencies") if isinstance(group, Mapping) else ()
    for env in _table(doc, "tool", "hatch", "envs").values():
        if isinstance(env, Mapping):
            yield from _strings(env.get("dependencies"))
            yield from _strings(env.get("extra-dependencies"))


def _setup_cfg(text: str) -> Iterator[str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return
    values = [parser.get("options", key, fallback="") for key in ("install_requires", "tests_require")]
    if parser.has_section("options.extras_require"):
        values += [v for _, v in parser.items("options.extras_require")]
    for value in values:
        yield from value.splitlines()


def _requirements_txt(text: str) -> Iterator[str]:
    for line in text.splitlines():
        stripped = line.split(" #", 1)[0].strip()
        if stripped and not stripped.startswith(("#", "-")):
            yield stripped


def python_names(rel: str, text: str) -> set[str]:
    """The normalised requirement names one Python declaration file declares."""
    base = rel.rsplit("/", 1)[-1]
    lines = (_pyproject(text) if base == "pyproject.toml" else _setup_cfg(text) if base == "setup.cfg"
             else _requirements_txt(text))
    return {name for line in lines if (name := _requirement(line)) is not None}


def _cargo_keys(table: Mapping[str, Any]) -> Iterator[str]:
    for key, value in table.items():
        package = value.get("package") if isinstance(value, Mapping) else None
        yield package if isinstance(package, str) else key


def cargo_names(text: str) -> set[str]:
    """The crate names a ``Cargo.toml`` depends on, in any dependency table."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    tables = [_table(doc, t) for t in _CARGO_TABLES] + [_table(doc, "workspace", "dependencies")]
    tables += [_table(cfg, t) for cfg in _table(doc, "target").values() if isinstance(cfg, Mapping)
               for t in _CARGO_TABLES]
    return {name.replace("_", "-") for table in tables for name in _cargo_keys(table)}


def npm_names(text: str) -> set[str]:
    """The package names a ``package.json`` depends on."""
    try:
        doc = json.loads(text)
    except ValueError:
        return set()
    if not isinstance(doc, dict):
        return set()
    return {"fast-check" if name.startswith("@fast-check/") else name
            for t in _NPM_TABLES if isinstance(doc.get(t), dict) for name in doc[t]}


def maven_groups(text: str) -> set[str]:
    """The ``groupId`` of every ``<dependency>`` in a ``pom.xml``; none when it does not parse."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return set()
    out: set[str] = set()
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag.rsplit("}", 1)[-1] == "dependency":
            for child in node:
                if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == "groupId" and child.text:
                    out.add(child.text.strip())
    return out


def gradle_groups(rel: str, text: str) -> set[str]:
    """The dependency groups a Gradle build file or version catalog names (module docstring)."""
    if rel.endswith(".toml"):
        try:
            libraries = _table(tomllib.loads(text), "libraries")
        except tomllib.TOMLDecodeError:
            return set()
        out: set[str] = set()
        for value in libraries.values():
            coord = value if isinstance(value, str) else value.get("module") if isinstance(value, Mapping) else None
            group = value.get("group") if isinstance(value, Mapping) else None
            if isinstance(coord, str):
                out.add(coord.split(":", 1)[0])
            if isinstance(group, str):
                out.add(group)
        return out
    code = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))
    return {m.group(1) for m in _GRADLE_COORD.finditer(code)} | {m.group(1) for m in _GRADLE_GROUP.finditer(code)}


def go_fuzz_tests(text: str) -> bool:
    """A ``_test.go`` file declares a Go fuzz test (module docstring)."""
    return bool(_GO_FUZZ.search(_RAW_STRING.sub("``", _BLOCK_COMMENT.sub("", text))))


def arms(names: Iterable[str], table: Mapping[str, str]) -> set[tuple[str, str]]:
    """``(practice, arm)`` for each of ``names`` that ``table`` lists."""
    return {(table[n], n) for n in names if n in table}


def jvm_arms(groups: Iterable[str]) -> set[tuple[str, str]]:
    """``(practice, arm)`` for each JVM group ``JVM_GROUPS`` lists."""
    return {(JVM_GROUPS[g][1], JVM_GROUPS[g][0]) for g in groups if g in JVM_GROUPS}
