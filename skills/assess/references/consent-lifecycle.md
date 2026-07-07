# Consent lifecycle: decline markers, three phases, non-interactive contract

Reference for `/assess`'s consent flow. SKILL.md Steps 2a/2b/2d and the assess-pr skill point here. Two concerns: how a permanent decline is recorded (decline markers with provenance), and how the offers are grouped so a user is never asked 8-10 serial questions (three phases) and a headless run never blocks (the non-interactive contract).

## Decline markers carry provenance

A user can permanently decline any optional tool (`scc`, a dead-code linter, the mutation pass) by writing `$REPO_ROOT/.assess/.no-<tool>`. A decline is a durable, silencing choice, so the marker records **who** declined **what**, **when**, and under **which plugin version** - not a provenance-free empty file. Always write markers with this helper so the disclosure and re-offer logic downstream has the data it needs:

```bash
# Resolve the plugin version for provenance stamping (degrades to "unknown").
assess_plugin_version() {
  local pj=""
<!-- chat-skip:start -->
  # Plugin install: read the version from the installed plugin.json.
  pj="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json}"
<!-- chat-skip:end -->
  if [ -n "$pj" ] && [ -f "$pj" ]; then
    jq -r '.version // "unknown"' "$pj" 2>/dev/null || echo unknown
  elif [ -f "$REPO_ROOT/.assess/run-context.json" ]; then
    jq -r '.plugin_version // "unknown"' "$REPO_ROOT/.assess/run-context.json" 2>/dev/null || echo unknown
  else
    echo unknown
  fi
}

# Write a JSON decline marker with provenance. $1 = tool; $2 = optional reason.
write_decline_marker() {
  local tool="$1" reason="${2:-}"
  mkdir -p "$REPO_ROOT/.assess"
  local who when ver
  who="$(git -C "$REPO_ROOT" config user.name 2>/dev/null || echo "${USER:-unknown}")"
  when="$(date +%Y-%m-%d)"
  ver="$(assess_plugin_version)"
  jq -n --arg by "$who" --arg at "$when" --arg ver "$ver" --arg reason "$reason" \
    '{declined_by: $by, declined_at: $at, plugin_version: $ver}
       + (if $reason == "" then {} else {reason: $reason} end)' \
    > "$REPO_ROOT/.assess/.no-$tool"
}
```

The marker JSON shape: `{declined_by, declined_at, plugin_version, reason?}` (`reason` optional). The deterministic core reads every `.no-<tool>` back into `run-context.json` as `decline_markers` (with a `decline_disclosures` line per marker and a `reoffer_mutation` flag). Two downstream effects, both automatic once you write markers this way:

- **Disclosure.** The report surfaces each active marker verbatim from `decline_disclosures`, e.g. _"Mutation testing permanently declined by ben on 2026-07-07"_ - a silenced capability is never invisible.
- **Re-offer once per major.** When a mutation marker was written under an older *major* plugin version, the core sets `reoffer_mutation: true`; Step 2d re-asks once. Declining again permanently restamps the marker at the current version, so its major now matches and the re-offer does not repeat within the same major.

**Pre-versioning markers** (empty or non-JSON files from before this convention) are still honoured as a decline; they read as "declined by an unknown user on an unknown date" and are never auto-re-offered (no major to compare). The `[ -f ... ]` presence checks in SKILL.md work identically for JSON and legacy markers.

## Three phases

`/assess` asks for consent at several points. Left un-batched, that is 8-10 serial questions - a tax the user pays one modal at a time. The flow is grouped into **three phases**, each a single decision surface:

- **Phase 1 - tool installs** (SKILL.md Steps 2a + 2b): **one** batched AskUserQuestion covering every optional analysis tool (`scc` plus each per-language dead-code linter). These are read-only system installs; batching them is safe because none modifies the repo.
- **Phase 3 - mutation pass** (SKILL.md Step 2d): kept **separate** and asked on its own. Unlike Phase 1, the mutation pass *modifies source and runs code*, so it carries a different risk class and must not be bundled into an install question where a user might wave it through. Frame it explicitly as code modification.
- **Phase 2 - write-back** (assess-pr Steps 5-7): **one** batched AskUserQuestion covering the four write-back offers (open a PR, track findings, freeze a CI gate, file feedback).

The phases are numbered by their risk grouping, not their run order: Phase 1 and 3 happen during Step 2; Phase 2 happens at the end.

## Non-interactive contract (headless / CI)

When no human can answer - a headless run, a CI job, any non-tty stdin - **every offer is treated as declined**. The run must complete with **zero interactive prompts**; never emit an AskUserQuestion that would block a pipeline forever. This holds in every phase.

Two enforcement surfaces, because the phases straddle the core run (Step 2c, which writes `run-context.json`):

- **Phase 1 (Steps 2a/2b) precedes the core** - `run-context.json` does not exist yet. Here the contract is **orchestration**: if this is a headless/CI run (no human to answer), make no Phase 1 AskUserQuestion calls, install nothing, write no markers. You determine this from your own runtime context, using the same condition the core will record.
- **Phases 3 and 2 (Steps 2d and the assess-pr write-back) follow the core**, so they read the authoritative flag the core computed with `sys.stdin.isatty() and not os.getenv("CI")`:

  ```bash
  jq '{interactive, offers}' "$REPO_ROOT/.assess/run-context.json"
  ```

  When `interactive` is `false`, **make no AskUserQuestion calls** - every offer (`tool_install`, `mutation`, `pr`, `issue_tracking`, `ci_gate`, `feedback`, `uninstall`) is already recorded as `{type, status: "skipped", reason: "non-interactive"}` in `offers`, the run's audit trail. When `true`, `offers` is empty and you drive the phases live.

The core cannot make AskUserQuestion calls for you, so *you* enforce "no prompts when non-interactive"; the `offers` array is the record that you did.
