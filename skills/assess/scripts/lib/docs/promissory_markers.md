# `lib/promissory_markers.py`

The full reference for `skills/assess/scripts/lib/promissory_markers.py`, whose module docstring holds the summary.

Write-side erosion instrument: detects the four families of promissory markers
(TODO/FIXME, deprecations, lint suppressions, disabled tests) via one rg pass per
family, then ages each marker by *survived touches* - the number of commits to its
file since the marker's introducing commit (batched `git blame` + one git-log
pass). A marker that survived many edits to an actively-maintained file is
unactioned intent; calendar age alone can't tell that from dormancy. Classifies
markers as tracked (issue/ticket/URL/date reference, or a justified suppression)
vs bare, and each introducing commit as agent/human (reusing `change_coupling`'s
conservative B4 identity rules). Marker text that is data is not a marker: in a
Python file `python_regions` places each hit, so one inside a string literal (a
test fixture, a pattern table) is dropped while a todo or deprecation in a
triple-quoted docstring still counts as prose about the code (a single-quoted
docstring opens with a quote, which the comment-context filter does not read as
a leader); a suppression counts only in a
comment and a disabled test only in code or a comment. A file that does not
tokenize, and every other language, keeps the line-based filters below. In any
code file a line-scoped suppression quoted in a backtick code span on a
comment-only line is a quotation (after code, ruff still reads it; a block-scoped
`rubocop:disable`, `pylint: disable` or ESLint block disable stays live quoted), and config files (TOML, YAML, INI,
JSON) carry no suppression family, since no listed directive is read from them.
A `todo` token counts only in marker position -
it opens its comment or prose line (after an optional bullet or checkbox) or a
leaderless docstring interior line, or carries a colon or a `(owner)` - so a
sentence that lists marker names is not a marker. In prose a comment leader opens
a marker only at line start, so an inline example (`` `* TODO` ``) is not one, and
a leaderless code-file line that opens with a bullet (a YAML enum's `- TODO`) is
data, not a marker. In a prose file a phrase alternative (`remove after`,
`temporary workaround`) inside an inline code span or quotation marks (straight
or curly) is a quoted example and does not count; a bare phrase on the same line
still does, and a code comment counts whatever the quoting. A justified
suppression (a ` - reason` or ` -- reason` starting with a word character, or a
non-empty `(reason)`, after the directive or its codes, or a trailing comment -
for `type: ignore[...]` the trailing `  # reason` is the only form mypy accepts,
and `pyright: ignore[...]` takes the same forms;
a trailing comment that opens with a directive or a todo token is not a
reason, so `# noqa: S603  # nosec B603` stays bare; the excluded directives are
the same `SUPPRESSION_DIRECTIVES` list the suppression family detects, so the
two cannot drift) is never stale and is counted in each family row's `justified`
(0 outside suppressions); other tracked markers still age, since an issue or a
deadline can go stale too. The `unactioned_intent` action states the
`stale_touches_threshold` it applied. Honours the shared excludes and the
generated-file filter (codegen `ignore_for_file` boilerplate is not debt), sets
aside every file `lib.path_classes.path_class` reads as a fixture (`fixtures/`,
`testdata/`, `resources/test/`, `mdtest/`, `snapshots/` ... at any depth: a linter's
fixtures are deliberate samples, #824) and reports `fixture_files_excluded`, the
distinct fixture files that held a candidate marker line in scope, and
degrades aging to `aging_reliable: False` on degenerate history (same verdict as
`git_churn`). The `rg` binary comes from `tool_lookup.find_rg`, resolved once per
scan; when none runs the scan is unavailable with `tool_lookup.RG_MISSING_REASON`.
Feeds the `unactioned_intent` derived finding, the hotspot pages' marker-debt
sentence, and the Layer 3/5/8 erosion rules. New ecosystem marker syntaxes need a
fixture in `tests/test_promissory_markers.py` - absence is a silent miss.
