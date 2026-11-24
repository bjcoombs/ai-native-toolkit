# `ACHIEVEMENTS_VERSION` history

The version history of `ACHIEVEMENTS_VERSION` in `skills/assess/scripts/lib/fleet/fleet_achievements.py`, newest entry first.
The release PR's `make fold` writes each new entry at the top from the notes in
[`skills/assess/changes/`](../README.md).

<!-- make fold writes each new entry below this line, newest first. -->

6 (4.0.0 release): two changes since 5.
- `recount.from_version` is `string | null` (issue #860): null when the re-scored entry
  stamped no maturity-model version, where it read the string `"none"` before; the
  Recount line then has no "(was ...)" clause.
- Steps read gates only (issue #1083): `TargetProgress` drops `threshold` and
  `below_threshold`; the report's target line names the gates to go and no count.

5 (3.0.0 release): one change since 4.
- targets name a Step (#945): ``Target`` and ``TargetProgress`` hold ``step``; progress gives
  ``lowest_step``, ``measured``, ``repos``, ``distance`` in Steps, ``threshold``, ``held``,
  ``below_threshold`` and ``missing_gates``. ``target_hit`` evidence gains ``from_step``,
  ``to_step`` and ``target_step``. The row level and mean share targets of #704 and #780 are
  removed: ``TargetProgress`` drops ``row``, ``target``, ``share_target``, ``level``, ``share``,
  ``distance`` in levels and ``share_distance``, and ``Evidence`` drops ``share_from``,
  ``share_to`` and ``target_share``.
  Breaking: readers of the previous version cannot read the removed fields, so the release
  PR bumps ``ACHIEVEMENTS_VERSION`` (frozen during run 4). ``target_hit`` compares with the
  latest earlier run measuring the same repositories, so a failed scan before the crossing
  never loses the hit.

4 (#772): ``personal_best`` retired; ``first`` off by default.

3 (#771): ``recount`` (lib/fleet/fleet_rescore.py), the rule-caused moves of a maturity-model bump.

1 (#704): first version. 2 (#780): share targets (``Evidence.target_share``,
``TargetProgress.share_target``/``share``/``share_distance``, ``target`` nullable).
