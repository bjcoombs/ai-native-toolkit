"""Check an SVG written by a Windows smoke run (.github/workflows/windows.yml).

Usage: python check_svg.py SVG [SVG ...]

Passes when each file is non-empty, decodes as UTF-8, contains `<svg` and
parses as XML whose root element is an SVG `svg` element.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def check(path: Path) -> str:
    assert path.is_file(), f"{path} was not written"
    data = path.read_bytes()
    assert data, f"{path} is empty"
    text = data.decode("utf-8")
    assert "<svg" in text, f"{path} has no <svg element"
    root = ET.fromstring(data)
    assert root.tag in ("svg", "{http://www.w3.org/2000/svg}svg"), f"{path}: root is {root.tag}"
    return f"{path}: {len(data)} bytes, root {root.tag}"


def main(argv: list[str]) -> int:
    assert argv, "usage: check_svg.py SVG [SVG ...]"
    for name in argv:
        print(check(Path(name)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
