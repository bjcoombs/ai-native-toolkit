# Probes

Three probes settle D5 (umbrella for existing installs) and the WS9 eval gate. Run 4 September 2026 against `main` at 44bddbf, on this machine and this account. The probes are numbered as the programme PRD numbers them; they were executed 3, 1, 2 (Probe 3 is independent and needs no branch).

Every command below is recorded verbatim with its exit code. Output is untidied, with two disclosed exceptions: timestamps are stripped from the `[DEBUG]` lines in Probe 2 (noted again there), and absolute home-directory paths and file owners are redacted - the worktree path to `<repo>`, the plugin cache path to `~`, and the `ls -laR` owner and group columns to `<user>` and `<group>`. Nothing else was altered.

```
$ claude --version
2.1.260 (Claude Code)
Exit code: 0
```

## Probe 1

**Question.** From a GitHub-sourced marketplace - not `--plugin-dir`, which skips out-of-tree symlinks - does a meta-plugin whose `skills/<x>` **and** `agents/<x>.md` entries are symlinks into a sibling plugin list every symlinked skill and agent exactly once, with no `README` component? The plugins-reference names `skills/` links only, so the `agents/` dereference is the unknown.

**Name substitution.** The probe uses throwaway ids, not the real ones: marketplace `ant-probe`, family plugin `ant-assess` carrying skill `probeskill` and agent `probe-agent`, meta-plugin `ant-umbrella`. This keeps the user's installed `ai-native-toolkit@ai-native-toolkit` (marketplace `ai-native-toolkit`) untouched and avoids every name collision. The probe tests symlink dereference and the both-installed tie-break, neither of which depends on the ids.

**Setup.** A throwaway branch `probe-symlink-meta` cut from `main` at 44bddbf, carrying:

```
plugins/ant-assess/.claude-plugin/plugin.json      (name ant-assess, 0.1.0)
plugins/ant-assess/skills/probeskill/SKILL.md      (frontmatter name: probeskill)
plugins/ant-assess/agents/probe-agent.md           (frontmatter name: probe-agent)
plugins/ant-assess/README.md
plugins/ant-umbrella/.claude-plugin/plugin.json    (name ant-umbrella, 0.1.0)
plugins/ant-umbrella/skills/probeskill  -> ../../ant-assess/skills/probeskill
plugins/ant-umbrella/agents/probe-agent.md -> ../../ant-assess/agents/probe-agent.md
plugins/ant-umbrella/README.md
```

`.claude-plugin/marketplace.json` was replaced on that branch only with a marketplace named `ant-probe` whose entries are `ant-umbrella` (`source: "./plugins/ant-umbrella"`) and `ant-assess` (`source: "./plugins/ant-assess"`). Both symlinks were committed as git mode `120000`.

First validation run, before an `author` block and a marketplace `description` were added:

```
$ claude plugin validate .
Validating marketplace manifest: <repo>/.claude-plugin/marketplace.json

⚠ Found 3 warnings:

  ❯ description: No marketplace description provided. Adding a description helps users understand what this marketplace offers
  ❯ plugins[0] plugin.json → author: No author information provided. Consider adding author details for plugin attribution
  ❯ plugins[1] plugin.json → author: No author information provided. Consider adding author details for plugin attribution

✔ Validation passed with warnings
Exit code: 0
```

After adding both:

```
$ claude plugin validate .
Validating marketplace manifest: <repo>/.claude-plugin/marketplace.json

✔ Validation passed
Exit code: 0
```

The branch was committed and pushed to `origin`. No pull request was opened for it.

**Ref syntax.** `marketplace add` has no `--ref` flag; the source argument carries the ref:

```
$ claude plugin marketplace add --help
Usage: claude plugin marketplace add [options] <source>

Add a marketplace from a URL, path, or GitHub repo

Options:
  -h, --help           Display help for command
  --scope <scope>      Where to declare the marketplace: user (default),
                       project, or local
  --sparse <paths...>  Limit checkout to specific directories via git
                       sparse-checkout (for monorepos). Example: --sparse
                       .claude-plugin plugins
Exit code: 0
```

The `<owner>/<repo>#<branch>` form worked on the first attempt; no other form was needed, so no failed attempt is recorded here.

```
$ claude plugin marketplace add bjcoombs/ai-native-toolkit#probe-symlink-meta
Adding marketplace…Cloning via SSH: git@github.com:bjcoombs/ai-native-toolkit.git
Refreshing marketplace cache (timeout: 120s)…
Cloning repository (timeout: 120s): git@github.com:bjcoombs/ai-native-toolkit.git (ref: probe-symlink-meta)
Clone complete, validating marketplace…
Cleaning up old marketplace cache…
✔ Successfully added marketplace: ant-probe (declared in user settings)
Exit code: 0
```

```
$ claude plugin install ant-umbrella@ant-probe
Installing plugin "ant-umbrella@ant-probe"...✔ Successfully installed plugin: ant-umbrella@ant-probe (scope: user)
Exit code: 0
```

```
$ claude plugin details ant-umbrella@ant-probe
ant-umbrella 0.1.0
  Description: Throwaway meta-plugin used by the modernization symlink probe. Its skills and agents are symlinks into ant-assess. Not for release.
  Source: ant-umbrella@ant-probe

Component inventory
  Skills (1)  probeskill
  Agents (1)  probe-agent
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~121 tok   added to every session

Per-component (rounded)
  component    always-on  on-invoke
  probeskill         ~60        ~50
  probe-agent        ~60        ~30

  On-invoke cost is paid each time a skill or agent fires.
  Token counts are estimates and may differ from actual usage.
Exit code: 0
```

The unqualified form returns the same output, byte for byte:

```
$ claude plugin details ant-umbrella
ant-umbrella 0.1.0
  Description: Throwaway meta-plugin used by the modernization symlink probe. Its skills and agents are symlinks into ant-assess. Not for release.
  Source: ant-umbrella@ant-probe

Component inventory
  Skills (1)  probeskill
  Agents (1)  probe-agent
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~121 tok   added to every session

Per-component (rounded)
  component    always-on  on-invoke
  probeskill         ~60        ~50
  probe-agent        ~60        ~30

  On-invoke cost is paid each time a skill or agent fires.
  Token counts are estimates and may differ from actual usage.
Exit code: 0
```

```
$ claude plugin list | rg -n 'ant-|ai-native-toolkit|Version|Status|Scope'
3:  ❯ ai-native-toolkit@ai-native-toolkit
4:    Version: 1.56.0
5:    Scope: user
6:    Status: ✔ enabled
8:  ❯ ant-umbrella@ant-probe
9:    Version: 0.1.0
10:    Scope: user
11:    Status: ✔ enabled
Exit code: 0
```

The installed cache copy is the meta-plugin with both symlink targets materialised as regular files, not the repository. `ls -laR` of `~/.claude/plugins/cache/ant-probe/ant-umbrella/0.1.0` (`.in_use` is a runtime lock directory):

```
drwxr-xr-x@ 7 <user>  <group>  224  4 Sep 16:15 .
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 ..
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 .claude-plugin
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 .in_use
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 agents
-rw-r--r--@ 1 <user>  <group>  100  4 Sep 16:15 README.md
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 skills

.../0.1.0/agents:
-rw-r--r--@ 1 <user>  <group>  254  4 Sep 16:15 probe-agent.md

.../0.1.0/skills:
drwxr-xr-x@ 3 <user>  <group>   96  4 Sep 16:15 probeskill

.../0.1.0/skills/probeskill:
-rw-r--r--@ 1 <user>  <group>  317  4 Sep 16:15 SKILL.md
```

### Conclusion

Both symlinks dereference. `claude plugin details ant-umbrella@ant-probe` reports `Skills (1)  probeskill` and `Agents (1)  probe-agent` - each symlinked component listed exactly once - and the cached install holds `agents/probe-agent.md` and `skills/probeskill/SKILL.md` as regular files (mode `-rw-r--r--`, not `lrwxr-xr-x`). The `agents/` dereference the plugins-reference does not document therefore works the same way `skills/` does. No `README` component appears in the inventory; note the weaker half of that finding, which the probe does not strengthen: `README.md` sat at the plugin root, where it was never a component. The repo's current `README` leak comes from `commands/README.md` and `agents/README.md`, and this probe did not reproduce that shape. D5 is marked Decided on the dereference finding; R3 still owns removing the two component-directory READMEs.

The cache copy is the meta-plugin plus its dereferenced targets, roughly 700 bytes here, not a whole-repo copy - the cost D5 claims for the rejected hybrid root entry is confirmed absent for this option.

## Probe 2

**Question.** With the family plugin installed beside the meta-plugin, which skill resolves and what does `claude plugin details` report for each? No doc defines the tie-break.

**Setup.** Continues from Probe 1: `ant-umbrella@ant-probe` still installed, `ant-assess@ant-probe` added beside it. Because `ant-umbrella`'s `probeskill` is a symlink to `ant-assess`'s, the two skill files are byte-identical, so the resolved skill cannot be told apart by its content. Resolution was read from the registry instead.

```
$ claude plugin install ant-assess@ant-probe
Installing plugin "ant-assess@ant-probe"...✔ Successfully installed plugin: ant-assess (scope: user)
Exit code: 0
```

```
$ claude plugin details ant-umbrella@ant-probe
ant-umbrella 0.1.0
  Description: Throwaway meta-plugin used by the modernization symlink probe. Its skills and agents are symlinks into ant-assess. Not for release.
  Source: ant-umbrella@ant-probe

Component inventory
  Skills (1)  probeskill
  Agents (1)  probe-agent
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~121 tok   added to every session

Per-component (rounded)
  component    always-on  on-invoke
  probeskill         ~60        ~50
  probe-agent        ~60        ~30

  On-invoke cost is paid each time a skill or agent fires.
  Token counts are estimates and may differ from actual usage.
Exit code: 0
```

```
$ claude plugin details ant-assess@ant-probe
ant-assess 0.1.0
  Description: Throwaway family plugin used by the modernization symlink probe. Not for release.
  Source: ant-assess@ant-probe

Component inventory
  Skills (1)  probeskill
  Agents (1)  probe-agent
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~119 tok   added to every session

Per-component (rounded)
  component    always-on  on-invoke
  probeskill         ~60        ~50
  probe-agent        ~60        ~30

  On-invoke cost is paid each time a skill or agent fires.
  Token counts are estimates and may differ from actual usage.
Exit code: 0
```

Each plugin reports its own full inventory. Neither `details` output mentions the other, warns about a duplicate, or marks a component shadowed. The always-on totals (~121 and ~119) are charged separately, so a user holding both pays for `probeskill` and `probe-agent` twice.

The session registry was read from the `system.init` event of a non-interactive run - the first `stream-json` line, which carries the `slash_commands` and `agents` arrays. The command as executed:

```
$ claude --print --model haiku --output-format stream-json --verbose "hi" | head -1
Exit code: 0
```

That single JSON line is not reproduced here.

**Derived summary** - not transcript output. The `slash_commands` and `agents` arrays on that line were filtered for entries containing `probeskill` and `probe-agent`; the filter was a `jq`/`rg` stage applied to the JSON after the fact, and its exact text was not recorded, so it cannot be reconstructed. The entries it reported:

| Registry array | Entries matching |
|----------------|------------------|
| `slash_commands` | `ant-assess:probeskill`, `ant-umbrella:probeskill` |
| `agents` | `ant-assess:probe-agent`, `ant-umbrella:probe-agent` |

The same event carries 80 slash commands. Every plugin-supplied one is namespaced - the 58 unqualified entries are all built-ins (`clear`, `model`, `compact`, `init`), and the installed toolkit appears only as `ai-native-toolkit:assess`, `ai-native-toolkit:huddle` and so on. No bare `probeskill` entry exists in the registry for either plugin.

The debug log for a `/probeskill` run agrees that nothing was dropped:

```
$ claude --print --model haiku --debug --debug-file /tmp/probe_debug.log "/probeskill"
[DEBUG] Checking plugin ant-umbrella: skillsPath=exists, skillsPaths=0 paths
[DEBUG] Attempting to load skills from plugin ant-umbrella default skillsPath: ~/.claude/plugins/cache/ant-probe/ant-umbrella/0.1.0/skills
[DEBUG] Checking plugin ant-assess: skillsPath=exists, skillsPaths=0 paths
[DEBUG] Attempting to load skills from plugin ant-assess default skillsPath: ~/.claude/plugins/cache/ant-probe/ant-assess/0.1.0/skills
[DEBUG] Loaded 1 agents from plugin ant-assess default directory
[DEBUG] Loaded 1 skills from plugin ant-umbrella default directory
[DEBUG] Loaded 1 agents from plugin ant-umbrella default directory
[DEBUG] Loaded 1 skills from plugin ant-assess default directory
[DEBUG] Total plugin skills loaded: 14 (0 duplicate/user-owned entries skipped)
Exit code: 1
```

(Timestamps stripped from the `[DEBUG]` lines above, and the cache paths redacted to `~` as disclosed in the preamble; every other character is verbatim. The lines are 41-52 and 58 of a 202-line log. The run exited 1 - the log's tail shows MCP servers aborting during teardown, not a skill-loading failure.)

A bare `claude --print "/probeskill"` did complete and answered from the skill, so bare invocation resolves to one of the two; which one is not observable non-interactively, and the two files are identical anyway. That run was not transcribed - no command output or exit code was captured for it, so it stands as a recollection, not evidence.

**Cleanup.**

```
$ claude plugin uninstall ant-assess@ant-probe
✔ Successfully uninstalled plugin: ant-assess (scope: user)
Exit code: 0
```

```
$ claude plugin uninstall ant-umbrella@ant-probe
✔ Successfully uninstalled plugin: ant-umbrella (scope: user)
Exit code: 0
```

```
$ claude plugin marketplace remove ant-probe
✔ Successfully removed marketplace: ant-probe
Exit code: 0
```

```
$ claude plugin list
Installed plugins:

  ❯ ai-native-toolkit@ai-native-toolkit
    Version: 1.56.0
    Scope: user
    Status: ✔ enabled

Exit code: 0
```

The throwaway branch was then deleted locally and on `origin`; `git branch -a` matches nothing named `probe-symlink-meta`.

### Conclusion

No tie-break exists, because nothing ties. Both plugins register their components under distinct namespaced ids - `ant-umbrella:probeskill` and `ant-assess:probeskill`, `ant-umbrella:probe-agent` and `ant-assess:probe-agent` - and the loader reports `0 duplicate/user-owned entries skipped`. Neither registration is dropped, shadowed, or warned about, and each plugin's `details` charges its own always-on cost, so a user holding both pays twice (~121 + ~119 tokens here) for one set of components. Bare-name resolution is not decided by any recorded rule: the registry holds no unqualified entry for either plugin, so the bare `/probeskill` is disambiguated inside the interactive matcher, which this probe could not observe.

This is what D5 already predicts and mitigates: the user who installs a family while keeping the umbrella double-registers, and the notice must tell them to install families first, then uninstall the umbrella. The probe adds one fact to that mitigation - the double registration is silent. Nothing in `install`, `list`, or `details` tells the user it happened.

## Probe 3

**Question.** Is `claude plugin eval` available on this account, or gated? This decides whether WS9's `evals.yml` reports neutral or runs.

**Setup.** An empty scratch directory outside any repository, with no plugin manifest and no `evals/` directory.

```
$ claude plugin eval init --bare sample
`plugin eval` is currently in early access
Exit code: 1
```

Nothing was written: the scratch directory was still empty afterwards.

```
$ claude plugin eval --help
Usage: claude plugin eval [options] [command] [target]

Run eval cases (<eval dir>/**/case.yaml or prompt.md + graders/*.md; the eval
dir is evals/ unless --eval-dir or the manifest says otherwise) against a plugin
and report scored results. Target is a path, a plugin name, or a
`plugin@marketplace` id — installed and skills-dir plugins both resolve (and add
a no-plugin baseline arm)

Options:
  --ablation <mode>         Run a no-plugin baseline arm and report the score
                            delta (none | with-without; default: with-without
                            whenever a plugin resolves — by name, or from the
                            target path — and none when nothing does; under
                            with-without, graders marked with-only, incl.
                            `tool_used: Skill`, are a plugin-fired indicator
                            rather than part of the score)
  --allow-tools <tools...>  Operator grant for gated tools (Bash, Write, Edit,
                            WebFetch, mcp__*). Supports Tool(pattern:*) syntax
  --case <glob>             Filter cases by name glob
  --eval-dir <dir>          Directory name (below the plugin) that holds the
                            eval cases; results go to <plugin>/<dir>/results/ —
                            for an installed-plugin target, ./<dir>/results/
                            with this flag, else ./evals/results/ (default dir:
                            the manifest's experimental.evals value, else
                            evals/)
  -h, --help                Display help for command
  --json [path]             Print the full run result (prompts, graders, per-run
                            scores) as JSON to stdout, or write it to this .json
                            file
  --judge-model <model>     Override LLM-grader model (default: haiku)
  --keep-temp               Preserve scaffold dirs for debugging
  --max-cost-usd <usd>      Optional hard cost ceiling; abort and report partial
                            results if hit (exit 2). Overrun is bounded to one
                            agent run — when that run breaches, paid graders
                            (llm/baseline) are skipped while free graders still
                            score it. Runs are already bounded by max_turns and
                            timeout_seconds — only set this when you need a
                            strict budget
  --mocks <mode>            Mock stand-ins for MCP servers, from <eval
                            dir>/mocks/ (record | off; default: record — off
                            spawns the real servers, gated by --allow-tools as
                            usual)
  --model <model>           Override model for all cases
  --no-publish              Keep the HTML report local only; skip publishing it
                            to claude.ai
  --no-scaffold             Explicitly skip scaffold_script
  --output-dir <dir>        Directory for aggregate-result.json (default:
                            ./<eval dir>/results/<timestamp>/)
  --publish-report          Also require publishing the report to claude.ai
                            (already the default when your account supports it);
                            explains why if unavailable
  --report <path>           Write the self-contained HTML report (scores,
                            prompts, grader verdicts) to <path> instead of the
                            results dir
  --runs <n>                Override per-case runs (default: case.runs ?? 3)
  --scaffold                Run each case's scaffold_script (runs
                            author-supplied bash as you; off by default — only
                            use on case files you authored)
  --tag <tag...>            Filter cases by tag (repeatable)
  --threshold <0..1>        Exit 1 if any case score is below this threshold
                            (default: 1.0)
  --verbose                 Log per-message trace events to the debug log (use
                            --debug-file to read them)

Commands:
  init [options] [name]     Author an eval suite under the eval dir (evals/
                            unless --eval-dir or the manifest says otherwise)
                            via an interview that sources inputs and designs
                            graders. Use --bare <name> for a blank single-case
                            template.
Exit code: 0
```

### Conclusion

`evals.yml` must report neutral (feature early-access-gated for this account). The line that decides it is `` `plugin eval` is currently in early access ``, printed by `claude plugin eval init --bare sample` at exit 1 - the command refuses before doing any work. `--help` exits 0 and prints the full option set, so the subcommand is present in the CLI and parses; presence is not entitlement, and a workflow that keys off `--help` succeeding would run a gated command and fail. WS9's `evals.yml` must therefore treat the early-access line as a neutral outcome rather than a failure, and the eval-case format under `evals/<type>-<slug>/{prompt.md, graders/criteria.md}` is confirmed by the `--help` text as the format the runner will read once the gate lifts.

The `--help` output also fixes two things WS9 depends on: exit 2 is a partial result (`--max-cost-usd` "abort and report partial results if hit (exit 2)"), and `--allow-tools` is the operator grant for `Bash`, needed only by the cases that shell out.
