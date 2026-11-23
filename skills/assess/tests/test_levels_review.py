"""lib/levels_fields.py: a condition's predicted outcome direction and review date (#1086)."""
from __future__ import annotations

import datetime as dt
import tomllib
from typing import Any

import pytest

import lib.level_tally as lt
from lib import catalog, catalog_md
from lib.level_signals import DEFAULT_LEVELS
from lib.levels import LevelsError, parse_levels
from lib.levels_fields import PREDICTIONS

REVIEW = dt.date(2027, 5, 23)


def _model(**fields: Any) -> dict[str, Any]:
    level = {"level": 1, "truth": ["docs"], **fields,
             "criteria": [{"signal": "doc_staleness.summary.stale_pct", "op": "eq", "value": 0}]}
    return {"version": "v1", "rows": [{"id": "r", "title": "R", "scope": "team", "aggregation": "min",
                                       "levels": [level]}]}


def test_the_predictions_are_the_outcome_measure_directions() -> None:
    assert PREDICTIONS == ("lower_revert", "lower_rework", "none")


@pytest.mark.parametrize("predicted", PREDICTIONS)
def test_a_prediction_and_review_date_load_onto_the_condition(predicted: str) -> None:
    level = parse_levels(_model(predicted=predicted, review=REVIEW))["rows"][0]["levels"][0]
    assert level["predicted"] == predicted
    assert level["review"] == "2027-05-23"  # ISO text, so the model stays JSON-serialisable


def test_a_toml_local_date_is_the_review_form() -> None:
    raw = tomllib.loads('version = "v1"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "team"\n'
                        'aggregation = "min"\n[[rows.levels]]\nlevel = 1\ntruth = ["docs"]\n'
                        'predicted = "lower_revert"\nreview = 2027-05-23\n'
                        'criteria = [{ signal = "doc_staleness.summary.stale_pct", op = "eq", value = 0 }]\n')
    assert parse_levels(raw)["rows"][0]["levels"][0]["review"] == "2027-05-23"


def test_without_them_the_condition_carries_neither() -> None:
    level = parse_levels(_model())["rows"][0]["levels"][0]
    assert "predicted" not in level and "review" not in level


def test_no_default_condition_has_a_review_date_yet() -> None:
    # Maintainer decision on #1086: no backfill; a condition PR sets its own date.
    raw = tomllib.loads(DEFAULT_LEVELS.read_text(encoding="utf-8"))
    assert not [lv for row in raw["rows"] for lv in row["levels"] if "review" in lv or "predicted" in lv]


@pytest.mark.parametrize(("fields", "message"), [
    ({"predicted": "higher", "review": REVIEW},
     r"row 'r' level 1: 'predicted' must be one of lower_revert, lower_rework, none \(got 'higher'\)"),
    ({"predicted": "lower_revert", "review": "2027-05-23"},
     r"row 'r' level 1: 'review' must be a TOML local date, YYYY-MM-DD unquoted \(got '2027-05-23'\)"),
    ({"predicted": "lower_revert", "review": dt.datetime(2027, 5, 23, 9, 0)},
     r"row 'r' level 1: 'review' must be a TOML local date"),
    ({"predicted": "lower_revert"}, r"row 'r' level 1: 'predicted' and 'review' go together; 'review' is missing"),
    ({"review": REVIEW}, r"row 'r' level 1: 'predicted' and 'review' go together; 'predicted' is missing"),
])
def test_a_bad_or_unpaired_field_fails_at_load(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        parse_levels(_model(**fields))


@pytest.mark.parametrize("stale_pct", [0, 5, None])
def test_the_fields_change_no_result(stale_pct: Any) -> None:
    data = {"doc_staleness": {"summary": {"stale_pct": stale_pct}}}
    plain = lt.level_result(parse_levels(_model()), data)
    reviewed = lt.level_result(parse_levels(_model(predicted="lower_rework", review=REVIEW)), data)
    assert reviewed == plain


def test_every_prediction_has_catalog_words() -> None:
    assert set(catalog_md.PREDICTION_WORDS) == set(PREDICTIONS)


def test_the_catalog_shows_the_prediction_and_review_date() -> None:
    raw = tomllib.loads(DEFAULT_LEVELS.read_text(encoding="utf-8"))
    raw["rows"][0]["levels"][0].update(predicted="lower_revert", review=REVIEW)
    rows = catalog._rows(parse_levels(raw), {}, {}, {})  # type: ignore[arg-type]
    first, second = rows[0]["levels"][0], rows[0]["levels"][1]
    assert (first["predicted"], first["review"]) == ("lower_revert", "2027-05-23")
    assert (second["predicted"], second["review"]) == (None, None)
    line = catalog_md.Line(rows[0], first, first["criteria"][0])
    assert catalog_md.review_cell(line) == "lower revert share, review 2027-05-23"
    assert catalog_md.review_cell(catalog_md.Line(rows[0], second, second["criteria"][0])) == "-"
