# WS6 spec: Guardrails

## Intent

Brief R14 of the programme in [../intent.md](../intent.md), with the notice text carried verbatim from brief R20; tag `modernization-guardrails`; indexed in [../README.md](../README.md).

## Current state

Measured at 44bddbf against this checkout. Every number below sits next to the command that produced it.

Nothing in the repository tree ships a hook today. There is no `hooks.json`, no `hooks/` directory, and no path with `hook` in its name:

```
$ git rev-parse HEAD
44bddbfa68d70685be3d6691bc17445ab98386ed

$ git ls-files "*hooks.json" | wc -l
       0

$ git ls-files "*hook*" | wc -l
       0
```

The single `plugin.json` declares no `hooks` field. Its seven keys are:

```
$ jq -r "keys | join(\", \")" .claude-plugin/plugin.json
author, description, homepage, license, name, repository, version
```

`CLAUDE.md` says nothing about hooks:

```
$ rg -c "hook" CLAUDE.md; echo "exit $?"
exit 1
```

One shipped file mentions a hook, and it is a description of a hook that lives in the user's personal `~/.claude` configuration, not in this repository:

```
$ rg -c "worktree-guard" skills/
skills/marathon/SKILL.md:1

$ rg -n --no-heading -o "worktree-guard.{0,58}" skills/marathon/SKILL.md
355:worktree-guard` PreToolUse hook blocks any command containing a `git com
```

That paragraph in `skills/marathon/SKILL.md` documents the personal hook's failure mode: it decides by the shell's cwd, so a lead whose `gh` polling left cwd in an integration checkout has a legitimate worktree commit rejected. The plugin hook specified here decides by the command's target repository instead, which is why R14 states that rule explicitly. The personal hook is out of scope for this workstream; only the repository tree is in scope.

There is no lint script for the lint hook to call:

```
$ git ls-files "scripts/*skill_lint*" "tests/*skill_lint*" | wc -l
       0
```

The `plugins/` tree the three `hooks.json` files land in does not exist until WS1 (PR1) creates it:

```
$ git ls-files "plugins/*" | wc -l
       0
```

The fixture-stdin tests join the repository-root `tests/` package, which today holds 14 Python files across three directories:

```
$ git ls-files "tests/*" | sed "s#/[^/]*$#/#" | sort -u
tests/
tests/canaries/
tests/canaries/jet-fighters/
tests/canaries/jet-fighters/build/
tests/canaries/jet-fighters/null_artifact/
tests/canaries/known-good-interactive/
tests/canaries/known-good-interactive/build/
tests/canaries/known-good/
tests/canaries/known-good/reference_implementation/
tests/canaries/vacuous-contract/
tests/contract/

$ git ls-files "tests/*.py" | wc -l
      14
```

That directory is run whole by the `plugin contract pytest` required check, so a test added under `tests/` is gated on merge with no workflow edit:

```
$ sed -n "50,60p" .github/workflows/tests.yml
    name: plugin contract pytest
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0  # v7.0.0

      - name: Install uv
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990  # v8.3.2

      - name: Run pytest
        run: uv run --with pytest pytest -v tests/
```

## Design

Three hooks, each in its owning plugin's `hooks/hooks.json` with a `description`. Plugin ownership follows the R1 component table cited in [../intent.md](../intent.md): the block hook is a delivery-workflow guardrail, the lint hook is a skill-authoring guardrail, and the notice belongs to the two plugins an existing 1.x install ends up holding under D5.

### Block hook (delivery)

`plugins/delivery/hooks/hooks.json`:

```json
{
  "description": "Blocks git commit and git push whose target repository is a <repo>-main integration checkout.",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(git *)",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/block_integration_write.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`block_integration_write.py` is a PEP 723 single-file script, matching the convention every other script in this repository uses. It reads the `PreToolUse` payload on stdin and:

1. Splits `tool_input.command` on `&&`, `||`, `;` and newline into segments.
2. For each segment whose first word is `git` and whose subcommand is `commit` or `push`, resolves the target repository in this order: an explicit `-C <path>`, `--git-dir=` or `--work-tree=` on that segment; otherwise the path of the most recent `cd <path>` segment earlier in the same command string; otherwise the payload's `cwd` field.
3. Runs `git -C <target> rev-parse --show-toplevel`.
4. Exits 2 when the resolved toplevel's basename ends in `-main`, printing the rule and the worktree command on stderr. Exits 0 otherwise.

The resolution order is the whole point of the design: `/tm` and `/fix-develop` legitimately `cd` into a `<repo>-main` checkout to run `checkout`, `pull`, `branch` and `worktree add`, so a cwd test blocks correct work. Only `commit` and `push` are blocked, and only when they land in the integration checkout.

Step 3 fails open. When `rev-parse` fails, because the path does not exist, is not a repository, or the segment cannot be parsed, the handler exits 0. A guardrail that blocks the Bash tool on an unparsable string costs more than the write it might have caught.

Documented limits: blocked under `allowManagedHooksOnly`, which drops plugin hooks entirely; runs inside every subagent, so a teammate's commit is checked and the per-call cost multiplies across a team; sees only the literal command string, so a `git commit` inside a shell script file, a `bash -c` string, or a shell function is invisible; a path reaching the integration checkout through a symlink is caught only because `rev-parse` resolves it, and only when the path exists at hook time.

### Lint hook (skill-craft)

`plugins/skill-craft/hooks/hooks.json`:

```json
{
  "description": "Lints an edited SKILL.md: frontmatter parses, description under 1,024 characters, body under 500 lines, references one level deep.",
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "if": "Edit(**/SKILL.md)",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/scripts/skill_lint.py",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "if": "Write(**/SKILL.md)",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/scripts/skill_lint.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Two entries rather than one because `if` holds a single permission-rule expression and no documentation defines an OR form across two tool names. The `matcher` is a regex over the tool name and stays `Edit|Write` in both entries; the `if` narrows each to its own tool. A `Write` and an `Edit` in one turn each fire once, and the handler is idempotent.

`skill_lint.py` reads the `PostToolUse` payload on stdin, takes `tool_input.file_path`, exits 0 when that path does not end in `SKILL.md`, and otherwise asserts four things: the frontmatter parses as YAML and carries `name` and `description`; `description` is under 1,024 characters; the body after the frontmatter is under 500 lines; every relative link from the file is at most one directory deep (`references/<x>.md`, `scripts/<x>`), never `references/<a>/<b>.md`. Failures print on stderr and the script exits 2.

Documented limits: exit 2 is the only exit code `PostToolUse` surfaces to Claude and it does not undo the write, so this is advice fed back into the turn, not a block; the hook does not fire on Bash-driven edits (`sed -i`, a heredoc redirect, `git apply`), because the matcher is the tool name; it lints the one file the tool touched, so a change that pushes a sibling file over a limit is not seen.

### Migration notice (assess 1.57.0 and the meta-plugin 2.0.0)

`plugins/assess/hooks/hooks.json` and `plugins/ai-native-toolkit/hooks/hooks.json`, identical apart from the `description`:

```json
{
  "description": "Shows the 2.0 migration notice once per major version at session startup.",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/migration_notice.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`migration_notice.py` reads `CLAUDE_PLUGIN_DATA` from the environment. When the variable is unset, or the marker `${CLAUDE_PLUGIN_DATA}/notice-2` already exists, it prints nothing and exits 0. Otherwise it creates the marker's parent directory, writes the marker, prints this object on stdout, and exits 0:

```json
{
  "systemMessage": "ai-native-toolkit 2.0 installed. Nothing you use has changed: /assess, /huddle, /deslop and every /ai-native-toolkit:<name> command still work exactly as before. The toolkit is now six plugins: assess, huddle, deslop, skill-craft, gh-org, delivery. This umbrella loads all six (~3,000 tokens per session). To keep only what you use (~1,000 tokens): /plugin install assess@ai-native-toolkit then /plugin uninstall ai-native-toolkit. Swap `assess` for any family above; install every family you use before uninstalling the umbrella. Full table: https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/migration-2.0.md. This message shows once.",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "ai-native-toolkit 2.0 installed. Nothing you use has changed: /assess, /huddle, /deslop and every /ai-native-toolkit:<name> command still work exactly as before. The toolkit is now six plugins: assess, huddle, deslop, skill-craft, gh-org, delivery. This umbrella loads all six (~3,000 tokens per session). To keep only what you use (~1,000 tokens): /plugin install assess@ai-native-toolkit then /plugin uninstall ai-native-toolkit. Swap `assess` for any family above; install every family you use before uninstalling the umbrella. Full table: https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/migration-2.0.md. This message shows once."
  }
}
```

`systemMessage` reaches the user and `additionalContext` reaches Claude, so a user who asks "what changed?" gets an answer from the same text rather than a second explanation. The marker basename is keyed to the notice generation: `notice-2` for the 2.0 notice, `notice-3` for any 3.0 successor. R14 records why the marker is a file and not frontmatter: `once: true` exists only in skill frontmatter.

Documented limits: the `startup` matcher fires on a new session only, so `--resume` and `--continue` sessions (matcher `resume`) never see the notice; `CLAUDE_PLUGIN_DATA` is per plugin, so a user holding both the umbrella and `assess` sees the notice once from each; a user who clears the data directory sees it again; the 5 second timeout drops the notice silently for that session, which is the correct trade because the hook always exits 0 and never delays startup.

### No dependency hook

R14 originally carried an assess dependency hook. The programme's Out of scope section cuts it: assess scripts are PEP 723 single-file scripts and `uv run` resolves their inline dependencies into uv's own cache. There is no manifest to diff and no first-run cost to save, so the hook would fire on every session to do nothing. It is cut, not deferred, and no WS6 PR ships one.

## Requirements

1. `plugins/delivery/hooks/hooks.json` exists with a `description` and a single `PreToolUse` entry, `matcher: "Bash"`, `if: "Bash(git *)"`. Verified by `jq` assertions in `tests/test_hook_contracts.py`.
2. The block handler exits 2 for `git commit` and for `git push` whose resolved target repository is a `<repo>-main` checkout, and exits 0 for the same commands targeting a worktree, and for `git checkout`, `git pull`, `git branch` and `git worktree add` targeting the integration checkout. Verified by the fixture-stdin cases in requirement 8.
3. The block handler resolves the target from `-C`, then a preceding `cd`, then the payload `cwd`, in that order, and exits 0 when `rev-parse` fails. Verified by fixture cases covering each branch.
4. The block message names the rule and the worktree command. Asserted on stderr by the fixture tests.
5. `plugins/skill-craft/hooks/hooks.json` exists with a `description` and two `PostToolUse` entries, `matcher: "Edit|Write"`, `if` values `Edit(**/SKILL.md)` and `Write(**/SKILL.md)`. Verified by `tests/test_hook_contracts.py`.
6. `plugins/skill-craft/scripts/skill_lint.py` exits 2 with the failing rule on stderr for each of the four checks and exits 0 for a compliant `SKILL.md`. Verified by the fixture cases in requirement 8.
7. `plugins/assess/hooks/hooks.json` and `plugins/ai-native-toolkit/hooks/hooks.json` each carry a `SessionStart` entry with `matcher: "startup"` and `timeout: 5`, and the notice handler exits 0 on every path. Verified by `tests/test_hook_contracts.py` and the fixture cases.
8. Each of the three hooks has a fixture-stdin test asserting exit code and message, under `tests/`, so the required `plugin contract pytest` check gates it with no workflow edit.
9. The notice text both handlers emit is byte-identical to the notice this spec carries in its Design section. `intent.md` transcribes the programme PRD's Problem Statement, Decisions, Deprecation Path, sequence and Success Criteria, not the R briefs, so this spec is the in-repo source of the R20 wording. Verified by `tests/test_hook_contracts.py`, which extracts the notice from the JSON block in `docs/design/2026-09-modernization/guardrails/spec.md` and compares it against both fields of both handlers' output.
10. `claude plugin validate plugins/<name> --strict` accepts each of the four plugins carrying a `hooks.json`, in the CI job R1 introduces. Recorded verdict: the paste from that job's first run on each WS6 PR.
11. No WS6 PR adds a dependency hook, and `docs/migration-2.0.md` carries no dependency-hook row.

## Verification

Fixtures live under `tests/fixtures/hooks/` and each is a recorded `PreToolUse`, `PostToolUse` or `SessionStart` payload piped to the handler on stdin.

`tests/test_hook_block_integration_write.py` over eight fixtures: `commit-in-main.json` (exit 2), `push-in-main.json` (exit 2), `commit-in-worktree.json` (exit 0), `git-c-main-commit.json` (exit 2, `-C` wins over `cwd`), `cd-main-then-commit.json` (exit 2, `cd` wins over `cwd`), `cd-worktree-then-commit.json` (exit 0, `cd` wins over a `cwd` in the integration checkout), `worktree-add-in-main.json` (exit 0, subcommand not blocked), `missing-path.json` (exit 0, fail open). The two exit-2 cases assert the stderr message contains both the rule and `git worktree add`.

`tests/test_hook_skill_lint.py` over five fixtures under `tests/fixtures/hooks/skill-lint/`: `valid/SKILL.md` (exit 0), `bad-frontmatter/SKILL.md`, `long-description/SKILL.md` (1,025 characters), `long-body/SKILL.md` (501 body lines), `deep-reference/SKILL.md` (a `references/<a>/<b>.md` link). Each failing case asserts exit 2 and the failing rule named on stderr.

`tests/test_hook_migration_notice.py` over three cases driven by a `tmp_path` `CLAUDE_PLUGIN_DATA`: first run writes the marker, prints the JSON and exits 0; second run prints nothing and exits 0; unset variable prints nothing and exits 0. The first-run case parses stdout and asserts `systemMessage` and `hookSpecificOutput.additionalContext` are both equal to the notice text this spec carries.

`tests/test_hook_contracts.py` asserts the structural shape of all four `hooks.json` files (requirements 1, 5, 7) and the notice byte-identity check (requirement 9).

`claude plugin validate` is the platform-side gate and cannot be asserted from pytest; its verdict is the paste from the R1 validation job, recorded in each WS6 PR body.

Open verification, resolved on the first real run rather than asserted here: whether `CLAUDE_PLUGIN_DATA` is populated in a plugin-hook environment. If it is not, the notice never shows, and the fallback is the marker under the plugin's cache root with the same basename.

## Breadcrumbs

The notice hook is itself the breadcrumb for the D5 split, not a shim that later needs one. Its marker is keyed per major, `notice-2`, so a 3.0 notice is a new marker and not a reset of this one.

WS6 adds one row to `docs/migration-2.0.md`, in the table WS1 lands the skeleton for: the migration notice, its `SessionStart` `startup` trigger, its marker path, and `remove-in: 3.0.0`. The notice is removed by WS10 alongside the meta-plugin, and its removal is one of the enumerated items in that spec.

The block and lint hooks are new guardrails, not compatibility shims. They carry no `remove-in` and add no migration-guide rows.

## Rollback

Each hook is one `hooks.json`, one handler, and its fixtures and test, in a directory nothing else reads. No skill body, script, workflow or manifest references a handler path, so `git revert` of any WS6 PR returns `main` to green with no follow-up edit.

Two constraints on the revert PR. First, the version bump: every WS6 PR bumps its owning plugin's `plugin.json` under R10, and the same-PR bump contract test compares against the base ref, so a revert PR bumps PATCH forward rather than restoring the previous number. Second, the notice marker is immutable once written: a user whose session already wrote `notice-2` has consumed the notice, so reverting and re-shipping the notice hook shows them nothing. Reverting the notice after `assess` 1.57.0 is released is therefore a partial rollback, and a re-ship needs a new marker basename to reach those users again.

## Sequencing

WS6 depends on WS1 (PR1), because `plugins/<name>/` does not exist until PR1 creates it. The three PRs are independent of each other and can run in parallel once PR1 has merged.

PR6a, block hook: `plugins/delivery/hooks/hooks.json`, `plugins/delivery/hooks/block_integration_write.py`, `tests/fixtures/hooks/*.json`, `tests/test_hook_block_integration_write.py`, `tests/test_hook_contracts.py`, and the delivery `plugin.json` and `CHANGELOG.md`. May not touch any other plugin, `scripts/floor_check.py`, `scripts/floor_anchor.py`, `.github/workflows/floor.yml`, `FLOOR.md`, or any CI job `name:` literal (D7).

PR6b, lint hook: `plugins/skill-craft/hooks/hooks.json`, `plugins/skill-craft/scripts/skill_lint.py`, `tests/fixtures/hooks/skill-lint/`, `tests/test_hook_skill_lint.py`, and the skill-craft `plugin.json` and `CHANGELOG.md`. May not touch any `SKILL.md` in the repository: the lint script asserts a standard the tree may not yet meet everywhere, and bringing bodies under 500 lines is WS4's work under R6, gated by `ab-equivalence`. May not touch the floor files or CI job names.

PR6c, migration notice: `plugins/assess/hooks/hooks.json`, `plugins/ai-native-toolkit/hooks/hooks.json`, both handlers, `tests/test_hook_migration_notice.py`, the `docs/migration-2.0.md` row, and the `assess` and umbrella `plugin.json` and `CHANGELOG.md`. Sequenced after the `docs/migration-2.0.md` skeleton lands in PR1, because the guide row needs a table to sit in. May not touch the floor files, CI job names, or any `action.yml` path.

If PR6a and PR6c both need to touch `tests/test_hook_contracts.py`, PR6a lands it and PR6c extends it; the file is the one shared surface across the three PRs.
