# `LEVEL_RESULT_VERSION` history

Fixture history of `LEVEL_RESULT_VERSION`, newest entry first.

<!-- make fold writes each new entry below this line, newest first. -->

3 (3.0.0 release): two changes since 2.
- a condition may carry ``polarity: "hazard"``, and a hazard is never counted (#843).
- owner exclusions add the optional ``excluded`` list and the condition and blocker
  fields ``disposition`` and ``exclusion_kind`` (#910); results without exclusions are
  unchanged.
