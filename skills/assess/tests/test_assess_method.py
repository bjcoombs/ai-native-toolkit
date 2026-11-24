"""lib.assess_method holds ASSESS_METHOD_VERSION; lib.artifact_schema re-exports it."""
from __future__ import annotations

from lib import artifact_schema, assess_method


def test_the_method_version_is_a_positive_integer() -> None:
    assert isinstance(assess_method.ASSESS_METHOD_VERSION, int)
    assert assess_method.ASSESS_METHOD_VERSION > 0


def test_artifact_schema_re_exports_the_method_version() -> None:
    assert artifact_schema.ASSESS_METHOD_VERSION == assess_method.ASSESS_METHOD_VERSION
