# `FLEET_ROW_VERSION` history

The version history of `FLEET_ROW_VERSION` in `skills/assess/scripts/lib/fleet/runner.py`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

2 (3.0.0 release): two changes, without a bump.
- ``dirty_reason`` (#932): a row written before it reads it as None through
  ``.get``.
- ``dirty_reason`` gains ``submodule_dirty`` (#1017), the one reason on a non-null
  ``dirty``: the row reads ``dirty: false`` and was scanned in place, but a
  submodule held uncommitted tracked edits. Additive: a row written before it
  reads that case as ``dirty_reason: null``.

2 (#569): ``active``. ``head_sha`` and ``dirty`` (#907) were added without a
bump: the history readers reject any other version, and a row written before
them reads both as None through ``.get``.
