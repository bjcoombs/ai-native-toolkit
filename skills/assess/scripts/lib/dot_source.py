"""On a CI line, the ``.`` builtin runs a script the way ``source`` does (issue #1033).

``dot_as_source()`` is a context manager (also usable as a decorator) under which
``dot_words(words)`` reads ``. x`` as ``source x``. ``lib.command_resolver.command_words``,
the tokeniser every command classifier uses, passes its words through ``dot_words``, and
``lib.ci_checks.scan_ci_checks`` is the only caller that runs inside ``dot_as_source``. So
``. ./test.sh`` in CI reads like ``source ./test.sh`` (Layer 5 and Layer 3), while an
instruction file that names ``. ./test.sh`` (Layer 0, ``agent_environment.check.named_total``)
reads as before. A bare ``.`` with no file is left alone. Pure, with no imports from ``lib``.
Tests: ``tests/test_dot_source.py``, ``tests/test_script_body.py``
(``test_layer_zero_reads_dot_as_on_main``).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_DOT_AS_SOURCE: ContextVar[bool] = ContextVar("dot_as_source", default=False)


def dot_words(words: list[str]) -> list[str]:
    """``words`` with a leading ``.`` read as ``source`` inside ``dot_as_source``."""
    return ["source", *words[1:]] if words[:1] == ["."] and _DOT_AS_SOURCE.get() else words


@contextmanager
def dot_as_source() -> Iterator[None]:
    """Read ``. x`` as ``source x`` in ``command_words`` inside the block (also a decorator)."""
    token = _DOT_AS_SOURCE.set(True)
    try:
        yield
    finally:
        _DOT_AS_SOURCE.reset(token)
