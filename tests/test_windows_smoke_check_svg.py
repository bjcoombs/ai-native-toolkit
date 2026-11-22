"""The SVG checker behind the Windows smoke step for the 3.12 SVG scripts (#968)."""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

CHECKER = Path(__file__).resolve().parent / "fixtures" / "windows_smoke" / "check_svg.py"
SVG = '<?xml version="1.0" encoding="utf-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"><title>a · b</title></svg>\n'


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_svg", CHECKER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_accepts_a_utf8_svg(tmp_path: Path) -> None:
    p = tmp_path / "ok.svg"
    p.write_bytes(SVG.encode("utf-8"))
    assert _load().main([str(p)]) == 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        SVG.encode("cp1252"),
        b'<?xml version="1.0"?>\n<html><svg xmlns="http://www.w3.org/2000/svg"/></html>\n',
        b"<svg",
    ],
    ids=["empty", "not-utf8", "root-not-svg", "not-xml"],
)
def test_refuses_a_bad_svg(tmp_path: Path, data: bytes) -> None:
    p = tmp_path / "bad.svg"
    p.write_bytes(data)
    with pytest.raises((AssertionError, UnicodeDecodeError, ET.ParseError)):
        _load().main([str(p)])


def test_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="was not written"):
        _load().main([str(tmp_path / "missing.svg")])
