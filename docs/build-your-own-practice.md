# Build your own practice

`/assess` scores a repository against a maturity model. The model is a TOML file of practices, and each practice reads signals that `/assess` already measures. Your team may hold a practice of its own, such as "a coverage gate of at least 80". You can add it to a copy of the model yourself.

This guide adds that one practice, runs it and reads the result. Each example file, result and error below is checked by a test (`skills/assess/tests/test_build_your_own_practice.py`), so they match the code.

## 1. Make a copy

Start from a model that is close to what you want. The toolkit ships three:

| Model | File | What it holds |
|-------|------|---------------|
| default | `skills/assess/levels/default.toml` | Every practice `/assess` scores, with the autonomy Steps. |
| handover | `skills/assess/levels/profiles/handover.toml` | Six practices a team expects before it takes a repository over. |
| citizen | `skills/assess/levels/profiles/citizen.toml` | A lighter bar while a citizen developer builds. |

The starter command writes a copy you can edit. Run it from `skills/assess` in a toolkit checkout, and give it a path in your own repository:

<!-- guide-example: copy -->
```sh
uv run scripts/levels_new.py /path/to/your-repo/my-team.toml --profile handover
```

Leave out `--profile handover` to copy the default model. The copy opens with a comment on each field. Its `version` is `my-team-1`, the file name with `-1` added. A custom model may not use a version that starts with `default-` or `profile-`, because the report uses those to name the shipped models. Change the version after each edit, so two results from different editions never look the same.

## 2. Add one practice

A practice is a `[[rows]]` table. Add this table to the end of `my-team.toml`:

<!-- guide-example: practice -->
```toml
[[rows]]
id = "coverage-80"
title = "Coverage gate of at least 80"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["behaviour"]
  criteria = [
    { signal = "coverage_gate.enforced_meets_minimum", op = "eq", value = true },
    { signal = "coverage_gate.max_threshold_percent", op = "gte", value = 80 },
  ]
```

What each line does:

- `id` is a short name for the practice, unique in the file. A Step gate uses it to name the practice.
- `title` is the name the report shows.
- `scope = "team"` reads each repository. An `"org"` row reads GitHub or GitLab group settings, and only `/fleet` has those.
- `aggregation = "min"` tells `/fleet` how to join many repositories: the level that every repository reaches.
- `level = 1` is the first level of the practice. A row can have levels 1, 2, 3 with no gap. A repository reaches a level when every criterion of that level, and of each level below it, holds.
- `truth` names what the practice keeps true. Coverage keeps the tests honest, so the tag is `behaviour`. The tags are listed in `skills/assess/scripts/lib/levels_fields.py`.
- `criteria` is a list of tests, and all of them must hold. Each test is `{ signal, op, value }`.

This practice has two tests. The first test asks that CI enforces a coverage gate of at least 50 percent. The second asks that the highest threshold in the repository is at least 80. Read together, CI enforces a gate, and some gate is set at 80 or more.

The operators are `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in` and `not_in`. `lt`, `lte`, `gt` and `gte` take a number, such as `80`, never a string such as `"80"`. `eq` and `ne` take one number, string, `true` or `false`. `in` and `not_in` take a list of one type, such as `["A", "B"]`.

## 3. Find the signal

Each signal is a path: a block name, a dot, then a field. The catalog, [What /assess measures](what-we-measure.md), lists every block and what it reads. The two signals above come from the [`coverage_gate` block](what-we-measure.md#coverage_gate-coverage-gate). The catalog names the signals the default model reads. Every other field of a block is described in `skills/assess/references/signals.schema.json`.

A wrong path stops the model before any scan, and the error names the fix. Here the second signal has a typo:

<!-- guide-example: typo -->
```toml
    { signal = "coverage_gate.threshold", op = "gte", value = 80 },
```

The run stops with this error. It names the line of the typo and the closest real signals:

<!-- guide-example: typo-error -->
```text
error: unknown signal path(s): team:coverage_gate.threshold (row 'coverage-80') at line 170, did you mean coverage_gate.max_threshold_percent, coverage_gate.threshold_meets_minimum or coverage_gate.configured?
```

## 4. Run it

In your repository, pass the file to `/assess` with `--levels`:

```sh
/assess --levels my-team.toml
```

For many repositories at once, `/fleet --levels my-team.toml` reads the same file. A single-repository run reads only the default model's blocks, such as `coverage_gate`; a model reading another block stops with an error naming it. Run such a model with `/fleet --levels` instead.

## 5. Read the result

The summary at the top of the report names your model first. Then it says whether every practice holds. For a repository whose CI enforces a gate of 60 percent, it reads:

<!-- guide-example: result-60 -->
```text
Maturity model: my-team.toml, a custom file, not the default.
Not every practice met (model: my-team.toml). Missing: Coverage gate of at least 80 (not met) [not in the default model].
Not in the default model: Coverage gate of at least 80.
```

Raise the gate to 85 percent and run it again. The second line changes:

<!-- guide-example: result-85 -->
```text
Maturity model: my-team.toml, a custom file, not the default.
Every practice met (model: my-team.toml).
Not in the default model: Coverage gate of at least 80.
```

The last line lists each practice your file adds to the default model. The lines below the summary give the autonomy Step, from the `[[steps]]` of your file. The new practice is not a gate of any Step, so it does not move the Step. The full result is the `level` block of `.assess/run-context.json`, where the practice is the condition `coverage-80.1`.

A repository with no coverage scan reads "not measured" for the practice, with the reason. An unknown value never counts as met, and it never counts as unmet.

## 6. What the format cannot say

The format is a list of tests that must all hold. Some rules need another shape:

- **OR.** One level cannot say "this test or that test". For one signal, use `in` with a list of values. For two signals, write two rows, and read the result of each.
- **NOT.** There is no NOT. Use the opposite operator: `ne` for `eq`, `not_in` for `in`, `lt` for `gte`. For a harm rather than a practice, add `polarity = "hazard"` to the level. The report lists a hazard that is met, and the tally never counts it.
- **Arithmetic.** A test cannot add, subtract or divide two signals. It can compare a signal with a fixed value. It can also check that two signals are equal, with `eq_signal` in place of `value`. Choose a signal that already holds the number you need, such as a percent.
- **A signal no scan measures.** The model reads only what the scans measure. "Every service has a runbook" needs a scan that looks for runbooks. Open an issue on the toolkit to ask for the signal.

When a rule needs one of these, the closest test is often good enough. Write its limit in a comment next to the row, so the next reader knows what the practice does not check.
