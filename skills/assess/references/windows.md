# Running /assess on Windows

A Claude Code session on Windows runs PowerShell, and uv is often absent on a
locked-down machine. Python 3.11+ (3.12+ for the two SVG scripts) and Git for
Windows are enough; no admin rights are needed.

## Dependencies without uv

The scripts declare their dependencies in a PEP 723 header that only `uv run`
reads. Install them once into the interpreter you run the scripts with:

```powershell
python -m pip install --user networkx grimp lizard==1.23.0 squarify matplotlib numpy
```

`assess_core.py` needs networkx and grimp; `assess_signals.py` needs networkx,
grimp and lizard; the treemap and doc graph need the rest. A missing package
leaves its block unavailable with a named reason (`no_grimp`, `no_lizard`), and
`assess_signals.py` and `fleet.py` print the install line for whatever is
missing.

## ripgrep

The promissory-markers scan (layer 3) runs ripgrep. `lib/tool_lookup.py` finds
it in this order:

1. `RG_PATH`, when it names the binary;
2. `rg` on `PATH`;
3. on Windows, `rg.exe` under
   `%LOCALAPPDATA%\Microsoft\WinGet\Packages\BurntSushi.ripgrep*` (the last in
   sorted order when several versions are installed).

So `winget install --scope user BurntSushi.ripgrep.MSVC` works even when policy
stops winget creating its `Links` alias. When none is found the block reads
`no_rg`, and its reason names the three lookups tried.

## The run sequence in PowerShell

`$AssessDir` is the skill directory, set in `SKILL.md` from the path Claude
Code fills in. Forward slashes work in PowerShell paths.

```powershell
$REPO_ROOT = git rev-parse --show-toplevel
New-Item -ItemType Directory -Force "$REPO_ROOT/.assess" | Out-Null

# rotate the prior stats sidecar
if (Test-Path "$REPO_ROOT/.assess/complexity-stats.json") {
  Copy-Item "$REPO_ROOT/.assess/complexity-stats.json" "$REPO_ROOT/.assess/complexity-stats.prior.json"
}

python "$AssessDir/scripts/complexity-treemap.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/complexity-heatmap.svg" --stats "$REPO_ROOT/.assess/complexity-stats.json"
python "$AssessDir/scripts/doc-graph-svg.py" "$REPO_ROOT" -o "$REPO_ROOT/.assess/doc-graph.svg"
python "$AssessDir/scripts/assess_core.py" "$REPO_ROOT"

# later steps follow the same pattern
python "$AssessDir/scripts/assess_finalize.py" "$REPO_ROOT"

# headless signals only
python "$AssessDir/scripts/assess_signals.py" "$REPO_ROOT" --out "$env:TEMP/signals.json" --counts-only
```

For a fleet run across many repositories, see the `fleet` skill's Windows
section.
