# Testing a branch as a plugin before merging

`/plugin install` from the git marketplace only ever sees `main`. To exercise an
unmerged branch's `SKILL.md` + bundled scripts the way a real user would, install
from a local checkout. There are two surfaces worth testing:

1. **The deterministic scripts** - fast, no install, catches logic regressions.
2. **The full plugin install** - exercises skill routing, the `$CLAUDE_PLUGIN_ROOT`
   script-path resolution, and the LLM following `SKILL.md`.

## 1. Run the scripts directly (no install)

The scripts are self-contained (PEP 723 + `uv`), so you can point them at any repo:

```bash
WT=~/dev/github.com/bjcoombs/ai-native-toolkit/worktree/<branch>   # the worktree
cd <target-repo>                                                   # repo under test
mkdir -p .assess
uv run "$WT/skills/assess/scripts/complexity-treemap.py"      "$PWD" -o .assess/complexity-heatmap.svg      --stats .assess/complexity-stats.json
uv run "$WT/skills/assess/scripts/docs-staleness-treemap.py" "$PWD" -o .assess/docs-staleness-heatmap.svg   --stats .assess/docs-staleness-stats.json
uv run "$WT/skills/assess/scripts/assess_core.py"            "$PWD"
# Then inspect the structured signals the LLM scores against:
jq 'keys' .assess/run-context.json
jq '.doc_graph, .doc_staleness.association, .stale_hubs[:5], .observability, .dead_code.tools' .assess/run-context.json
open .assess/complexity-heatmap.svg .assess/docs-staleness-heatmap.svg
```

Run the test suites too:

```bash
cd "$WT/skills/assess" && uv run --with pytest pytest -q   # deterministic core
cd "$WT/scripts"       && uv run --with pytest pytest -q   # standalone-build pipeline
```

## 2. Install the branch as a local plugin (non-destructive)

This keeps the released `ai-native-toolkit@ai-native-toolkit` install (from the git
marketplace) completely untouched, so revert is trivial.

A local directory **is** a valid marketplace source - `/plugin marketplace add <path>`
reads `.claude-plugin/marketplace.json` in place (no clone), so it reflects whatever is
checked out in the worktree. The one catch: marketplaces are keyed by their manifest
`name`, and the branch's `name` ("ai-native-toolkit") collides with the already-registered
git marketplace. So give the local copy a throwaway distinct name first.

**Setup** (a local edit you do **not** commit):

```bash
# In the worktree, .claude-plugin/marketplace.json:
#   "name": "ai-native-toolkit"  ->  "name": "ai-native-toolkit-dev"
```

**Install + test**, in a Claude Code session:

```
/plugin marketplace add /abs/path/to/worktree/<branch>
/plugin install ai-native-toolkit@ai-native-toolkit-dev
```

Then **restart Claude Code** - skills, commands and agents are discovered at session
start, so the new `SKILL.md` + scripts only load after a relaunch. Run `/assess` against
a target repo and confirm it completes end-to-end (this is where a broken script path
would surface).

**Revert** to the released version:

```
/plugin uninstall ai-native-toolkit@ai-native-toolkit-dev
/plugin marketplace remove ai-native-toolkit-dev
```
```bash
git checkout -- .claude-plugin/marketplace.json   # discard the throwaway rename
```

Restart once more. The released install was never modified. (Optionally
`rm -rf ~/.claude/plugins/cache/ai-native-toolkit/ai-native-toolkit/<new-version>` to drop
the staged cache dir - harmless to leave.)

## How a plugin skill finds its bundled scripts

A plugin skill loads from the **version cache** -
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md` - **not**
from `~/.claude/skills/`. So a skill must resolve its own scripts via
`$CLAUDE_PLUGIN_ROOT` (Claude Code sets it to the plugin root for Bash run in a plugin
context). `SKILL.md` does this with a fallback to a hand-placed `~/.claude/skills/assess/`
copy:

```bash
SKILL_DIR="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/assess}"
SKILL_DIR="${SKILL_DIR:-$(dirname "$(realpath ~/.claude/skills/assess/SKILL.md)")}"
```

(That whole block is inside `<!-- chat-skip -->` so it's stripped from the standalone
ZIP, which uses bare `scripts/...` paths instead. The build's integration tests assert
neither `SKILL_DIR` nor `CLAUDE_PLUGIN_ROOT` leaks into a standalone build.)

## Gotchas

- **Restart after every install/version change** - definitions load at session start.
- **Don't `/plugin marketplace update` the canonical clone** (`~/.claude/plugins/marketplaces/ai-native-toolkit`)
  while testing a branch checked out inside it - that command `git reset`s to `origin/main`
  and discards the checkout (and any local-ahead commits).
- **The `-dev` rename is what makes this non-destructive** - skipping it risks overwriting
  the released marketplace's source entry.
- **`uv` must be on PATH** for the scripts to resolve their PEP 723 dependencies.
