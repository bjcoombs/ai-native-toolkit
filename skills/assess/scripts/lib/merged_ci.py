"""The merged CI configuration a platform read gives the offline scans (issue #796).

The value type of the private ``gitlab_merged_ci`` registry spec. It lives apart from
``lib/gitlab_ci_lint.py``, which reads it through ``glab``, so ``ci_checks`` and
``review_automation`` stay offline modules: they import this type, never a platform
client. ``text`` is held in memory only and is never published.

The value type of the private `gitlab_merged_ci` registry spec (#796): `MergedCi(status,
reason, text, include_sources)`, `NOT_READ`, `NOT_NEEDED`, `MERGED_STATUSES` and
`as_merged(value)` (anything other than a `MergedCi`, such as a degraded spec's dict, is
`NOT_READ`). Apart from `gitlab_ci_lint.py` so `ci_checks` and `review_automation` stay
offline modules (`tests/test_signals.py` checks that a scan is `network` exactly when its
module reaches a platform client). `text` is held in memory only. Tests:
`tests/test_gitlab_ci_lint.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MergedStatus = Literal["not_read", "not_needed", "read", "invalid", "unavailable"]
MERGED_STATUSES: tuple[MergedStatus, ...] = ("not_read", "not_needed", "read", "invalid",
                                             "unavailable")


@dataclass(frozen=True)
class MergedCi:
    """One repository's merged CI configuration. ``text`` is held in memory only."""

    status: MergedStatus
    reason: str | None = None  # the reason code when unavailable; never the sentence
    text: str | None = None  # merged_yaml when read
    include_sources: tuple[str, ...] = ()  # component locations and project paths the merge pulled in

    @property
    def read(self) -> bool:
        return self.status == "read" and self.text is not None


NOT_READ = MergedCi("not_read")
NOT_NEEDED = MergedCi("not_needed")


def as_merged(value: object) -> MergedCi:
    """``value`` when it is a :class:`MergedCi`, else ``NOT_READ`` (a degraded spec's dict)."""
    return value if isinstance(value, MergedCi) else NOT_READ
