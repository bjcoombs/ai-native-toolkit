"""Every diagram part with a ``<title>`` carries its values as data attributes (#1360, #1312).

Static markup only, no DOM: each generator's golden render, plus the radar and the treemap
rendered now, is read as text by ``svg_data_attrs.title_data_problems``. The doc graph's real
render is checked in ``tests/test_doc_graph_svg.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.fleet.fleet_svg as svg
from svg_data_attrs import part_problems, title_data_problems, titled_parts
from test_diagram_scoping import _rendered


# Diagrams whose only <title> is the root's: nothing per part to carry.
ROOT_TITLE_ONLY = {"assess-step-ladder", "fleet-step-movement"}


def test_every_titled_part_of_every_diagram_carries_its_title_values(tmp_path: Path) -> None:
    rendered = _rendered(tmp_path)
    for name, markup, _scope in rendered:
        assert title_data_problems(markup) == [], name
    titled = {scope for _name, markup, scope in rendered if titled_parts(markup)}
    assert titled == {scope for _name, _markup, scope in rendered} - ROOT_TITLE_ONLY


@pytest.mark.parametrize("data, title, fragment", [
    ({}, "a: 3", "no data attribute"),
    ({"category": "unmet"}, "a: not met", "no data attribute"),
    ({"group": "b"}, "a: 3", "not in title"),
    ({"group": "a", "level": "4"}, "a: level 3", "not a number"),
    ({"group": "a"}, "a: level 3", "no data attribute carries"),
])
def test_a_part_missing_or_mismatching_a_value_is_named(data: dict[str, str], title: str,
                                                        fragment: str) -> None:
    assert any(fragment in problem for problem in part_problems(data, title))


def test_numbers_match_to_the_printed_precision_and_through_separators() -> None:
    assert part_problems({"path": "src/a.py", "loc": "1234"}, "src/a.py\n1,234 LOC") == []
    assert part_problems({"group": "g", "change": "-0.10"}, "g: (-0.10; 1 of 2)") != []
    assert part_problems({"group": "g", "change": "-0.10", "paired": "1", "rows": "2"},
                         "g: (-0.10; 1 of 2)") == []
    assert part_problems({"path": "docs/v2.md"}, "docs/v2.md") == []


def test_text_tooltips_are_repeated_as_data_label() -> None:
    out = svg.text(0, 0, "Long…", tooltip='Long "name" & more')
    assert 'data-label="Long &quot;name&quot; &amp; more"' in out
    assert "data-label" not in svg.text(0, 0, "Short")


def test_data_attrs_leaves_out_a_value_not_measured_never_writing_zero() -> None:
    assert svg.data_attrs({"level": 0, "top": None, "path": "a<b"}) == ' data-level="0" data-path="a&lt;b"'
