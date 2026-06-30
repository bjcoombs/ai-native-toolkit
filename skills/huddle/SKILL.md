---
name: huddle
description: "Structured multi-perspective analysis using Six Thinking Hats with professional lens team members. TRIGGER when the user types /huddle, asks to run a huddle, wants a panel/board/team to analyze a decision, asks for multi-perspective analysis, debate, or red-team/blue-team review, or wants to weigh a hard call from several angles. Scales from solo (1 agent) to board-level (8+) using Fibonacci sizing."
---

# Huddle - Six Thinking Hats Analysis

Scales from a solo gut check to a board-level deliberation using Fibonacci team sizing.

## Architecture

**You are Blue Hat** - the chair. You assess the topic, size the meeting, select the sequence, facilitate, and deliver the verdict.

<!-- chat-replace:hat-source -->
**Hat agents** (`white-hat`, `red-hat`, `black-hat`, `yellow-hat`, `green-hat`) are methodology specialists in `~/.claude/agents/`, dispatched via the `Agent` tool with `subagent_type=<hat>`.

**Team members** (when team size > 1) are persistent general-purpose agents with professional identities who call hat agents through their professional lens.

## Hat Findings Schema

Every hat agent returns its findings as one structured object - the unit the chair synthesises, the critic reviews, and the discovery loop tests for new claims. It is the same shape in all three execution modes (solo, phased, team).

```json
{
  "lens": "",
  "hat": "white|red|black|yellow|green",
  "claims": [
    { "claim": "", "severity_or_value": "HIGH|MEDIUM|LOW | positive | neutral", "evidence": "" }
  ]
}
```

- `lens` - the professional lens the finding came through (e.g. `security-eng`); empty/`blue` for solo, where the chair runs the hats directly.
- `severity_or_value` - reads by hat: **Black** uses risk severity (`HIGH`/`MEDIUM`/`LOW`); **Yellow**/**Green** use opportunity value (`positive`/`neutral`); **White** facts carry no severity (`neutral`); **Red** records the gut-check signal in the same field (e.g. `HIGH` unease, `positive` pull).
- `evidence` - the file, quote, datum, or reasoning the claim rests on. An empty `evidence` is what the completeness-critic flags as an unverified claim.

## Capability Requirements

<!-- chat-replace:execution-mode-rule -->
Three execution modes exist. Pick one **deterministically**: team size = 1 → **solo flat-parallel**; team size ≥ 2 AND you can confirm the team-mode capability (`SendMessage` plus background `Agent` teammates) is available → **team mode**; otherwise → **phased sub-agent mode**. Confirming availability means actively probing, not glancing at your visible tools - `SendMessage` may be deferred behind `ToolSearch` (see the capability-detection step). If, after probing, you still cannot reach team mode, default to phased - it degrades gracefully, whereas attempting team mode without the capability fails loudly.

| Mode | When chosen | Mechanism | Cost (relative) |
|------|-------------|-----------|----------------|
| **Solo flat-parallel** | Size 1 | Hat agents fire in parallel via standard Agent tool; Blue Hat synthesises | 1× |
| **Phased sub-agent** | Size 2+, no flag | Iterate phases sequentially; spawn N sub-agents per phase (one per lens), each with fresh context, briefed via a running synopsis Blue Hat maintains | 2-4× |
<!-- chat-replace:team-mode-row -->
| **Team mode** | Size 2+, flag enabled | Persistent background `Agent` teammates in one implicit team cross-talk via `SendMessage` across phases | 5-15× |

<!-- chat-skip:start -->
**Agent Teams flag** enables `SendMessage` and the background-teammate mechanism (`Agent` spawned with `run_in_background: true`, joined into one implicit team). Enable in your environment:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Without the flag, `/huddle` still runs multi-perspective deliberations via phased sub-agent mode - it just trades cross-talk between agents for a running synopsis Blue Hat maintains as the persistent memory. Quality drops a little, cost drops a lot.

**Deferred-tool caveat (the silent-fallback trap).** With the flag enabled, newer Claude Code builds do not list `SendMessage` in your live tool set - they defer it behind `ToolSearch`, surfacing only its name in a system-reminder. If you decide between team and phased mode by glancing at your visible tools, you will wrongly conclude team mode is unavailable and degrade to phased with no error. Resolve it at the capability-detection step below by loading the schema with `ToolSearch("select:SendMessage")` before you decide - a successful load is confirmation that team mode is reachable. (This build forms a **single implicit team**: you spawn named background `Agent` teammates and they join automatically, so `SendMessage` is the one capability that gates team mode.)

**Why enable team mode anyway:** persistent professional-lens agents talking to each other across phases produce noticeably deeper synthesis - disagreements get rebutted in real time, edge cases surface from cross-talk, and the verdict feels like real deliberation rather than serially-summarised opinions. Worth it for decisions where being wrong costs 100× more than the analysis: architecture choices, irreversible migrations, hiring calls, contractual commitments.

**One team per session.** This build allows exactly one implicit team per Claude Code session, and the main session is its permanent lead. A team-mode huddle claims that single team - so do not start a second team-mode skill (another huddle, a `/tm` marathon) in the *same* session: its teammates would land in the same team, sharing one task list and one mailbox with the huddle's hats. To run two team-mode workstreams at once (e.g. a huddle defining the next PRD while a marathon implements the current one), use a *separate* session - a second terminal, ideally its own worktree. Each session gets its own isolated team (`session-<id>`-named), lead, task list, and mailbox.
<!-- chat-skip:end -->

<!-- chat-replace:capability-detection -->
**Tell the user which mode you're in** before you start, but probe for the team capability first - do not treat "not in my visible tool list" as "unavailable". Newer Claude Code builds defer `SendMessage` behind `ToolSearch`: when the flag is enabled it is listed only by name in a system-reminder, not as a directly-callable tool, so a naive availability check false-negatives and silently drops you into phased mode. If it is not already live, run `ToolSearch("select:SendMessage")` to load its schema and treat a successful load as confirmation. (This build uses a single implicit team that named background `Agent` teammates join on spawn; `SendMessage` is what gates the cross-talk.) If it loads (or is already callable) and team size ≥ 2, announce team mode - one line, e.g. "Running in team mode (3 professional lenses, 5 phases - persistent agents)." Announce phased sub-agent mode only if the probe genuinely fails to surface it, e.g. "Running in phased sub-agent mode (3 lenses, 5 phases)."

## No Arguments Behavior

If called without arguments, respond with:
"What topic or problem would you like to analyze?"

## Protocol

### Step 1: Frame the focus, then analyze the topic

You are the opening Blue Hat here, and the opening Blue has two jobs: define the focus, then set the process. Do the focus first - it is the cheapest phase (one chair pass, no spawns) sitting at the highest-leverage point, because every hat and lens downstream inherits the frame. A flawless five-hat deliberation on the wrong problem is the one failure no later hat can catch.

**Frame the focus.** State what is being deliberated as a one-sentence **Topic line** that names the *problem*, not a solution. Run the premise check: does the topic name a problem, or pre-select an answer? If it embeds a solution (e.g. "should we migrate to microservices?" pre-selects microservices), name the underlying problem and demote the proposed solution to one option the hats will weigh. A frame that names a solution has smuggled Yellow/Black judgement into the setup phase, out of sequence - so this is the huddle refusing to execute a handed premise unexamined.

The Topic line is **provisional**. When you run the White Hat phase, hand it the Topic line and ask it to flag if the facts reframe the problem - White is the hat positioned to catch a wrong frame on evidence. If any hat reframes, update the Topic line and carry the move forward: a reframe is the framing step working, not failing.

Then assess the topic to determine:

1. **Team size** - Fibonacci numbers scale naturally with complexity:

   | Size | Mode | Use |
   |------|------|-----|
   | **1** | Solo | Quick analysis. You spawn hat agents in parallel, synthesize yourself. Cheapest tier. |
   | **2** | Debate | Two opposing lenses. Forced productive tension. |
   | **3** | Huddle | Sweet spot. Most deliberations. |
   | **5** | Panel | Broader perspectives. Cross-functional decisions. |
   | **8+** | Board | Major strategic decisions. Use sub-groups that report to the chair. |

   Odd numbers (1, 3, 5, 13) give natural voting balance. Even numbers (2, 8) force productive tension through disagreement. Use your judgement.

2. **Professional lenses** (for size 2+) that create productive tension for THIS topic. You know what expertise fits - choose what creates the most useful disagreement.

3. **Hat sequence** based on problem type:
   - Quick: Red only (gut check, 30 seconds)
   - Simple: White → Black (2 phases)
   - Moderate: White → Red → Black → Yellow → Green (5 phases)
   - Complex: White → Red → Black → Yellow → Green (5 phases, deeper investigation)
   - **Red Hat is always available.** Permission to ask "what does my gut say?"
   - Adapt the sequence to the topic. These are guides, not straitjackets.

Announce your team composition, sequence, **and the Topic line** to the user before proceeding. Surfacing depth rides the same stakes dial as sizing - framing always happens; how loudly you surface it scales:

- **Size 1:** state the Topic line in your announcement and proceed - no validation handshake.
- **Size 2+:** lead the announcement with the Topic line so the user can redirect a wrong frame before any spend.
- **Solution-laden topic (any size):** surface the reframe to the user regardless of size - a smuggled solution is the one failure the whole huddle would otherwise execute flawlessly, so even the gut-check tier flags it.

Stating the frame is a statement, not a blocking gate.
<!-- chat-skip:start -->
In autonomous/headless runs (`/tm`, `/issues`, marathon) state the Topic line and proceed without waiting for confirmation; it is logged in the verdict for later inspection, never a stall.
<!-- chat-skip:end -->

### Branch by capability

After Step 1 you have a team size, a list of professional lenses, and a hat sequence. How you execute them depends on which capability is available:

| Configuration | Execution mode |
|---|---|
| Size 1 | **Solo flat-parallel** (below) |
<!-- chat-skip:start -->
| Size 2+ AND Agent Teams flag enabled | **Team Mode** (Step 2 onwards) |
<!-- chat-skip:end -->
<!-- chat-replace:branch-phased-row -->
| Size 2+ AND Agent Teams flag NOT enabled | **Phased Sub-Agent Mode** (below) |

### Solo flat-parallel (Size 1)

Spawn hat agents in parallel based on your chosen sequence. Each agent operates independently on the topic and returns a Hat Findings object (see schema). Collect the structured findings, then synthesize as Blue Hat: group claims by hat, rank each group by `severity_or_value`, and cross-reference evidence across lenses to spot agreement and contradiction. No team, no discussion - just parallel analysis.

### Phased Sub-Agent Mode (Size 2+, no team flag)

<!-- chat-skip:start -->
When team size > 1 but team mode is unavailable, do not collapse to flat-parallel - that throws away both phase ordering and multi-lens diversity. Instead, iterate phases sequentially and spawn fresh sub-agents per phase:
<!-- chat-skip:end -->
When team size > 1 but team mode is unavailable, do not collapse to flat-parallel - that throws away both phase ordering and multi-lens diversity. Instead, iterate phases sequentially and spawn fresh sub-agents per phase:

**Loop over each hat phase in your sequence:**

1. **Announce the phase to the user.** "Phase 3 of 5: Black Hat - risks."
2. **For each professional lens, spawn a sub-agent in parallel** for this phase. Each sub-agent gets:
   - Its persona (the professional lens you assigned in Step 1)
<!-- chat-replace:phased-spawn-instructions -->
   - The hat methodology for this phase (`Agent` tool with `subagent_type=<hat>` resolves the agent file from `~/.claude/agents/`)
   - The topic
   - **A running synopsis you maintain as Blue Hat** - a 200-400 word summary of what every prior phase produced. This is how cross-phase continuity survives without a persistent team. Each sub-agent has a fresh context window, so the synopsis is its only memory of what came before.
3. **Collect structured findings.** Each sub-agent returns a Hat Findings object (see schema). As Blue Hat, write a 100-200 word phase summary capturing: each lens's claims grouped by `severity_or_value`, where claims conflicted, what changed your view. Append this to the running synopsis.
4. **Surface the phase summary to the user** before moving on. Short, scannable.

**When to collapse to one sub-agent per phase voicing all lenses.** Default is one sub-agent per lens per phase (preserves independent fresh contexts). But total spawns = `team_size × phases` - a size-5 board × 5 phases = 25 sub-agent calls. When that count exceeds ~8-10, or when running in a chat UI where spawn latency is user-visible, collapse to **one sub-agent per phase voicing all lenses**. When you do, **explicitly instruct that sub-agent to keep the lenses cognitively distinct** - separate labelled sections per lens, no blending. Without that instruction the lenses blur and you lose most of the multi-perspective value. This is a degraded fallback, not a third mode; reach for it deliberately.

**At the end of all phases**, deliver the verdict as in team mode (Step 5 below): one paragraph stating the decision, the strongest dissent, and the conditions under which you'd reverse.

**Why this works without team mode:**

- Each sub-agent gets a clean context window (the equivalent benefit to a persistent team member's fresh perspective).
- Multi-lens diversity preserved - N professionals still weigh in per phase.
- Phase ordering preserved - Black Hat sees White Hat's facts via the synopsis.
<!-- chat-skip:start -->
- No `SendMessage` or background teammates required; uses only foreground Agent calls.
<!-- chat-skip:end -->
- No team mode infrastructure required; uses only the standard Agent tool.

<!-- chat-skip:start -->
**Cost:** roughly 2-4× flat-parallel (see Capability Requirements table). The synopsis grows with each phase (200-400 words per phase → up to ~2KB by phase 5), and that growing synopsis is passed into every sub-agent on every subsequent phase, so the cost skews late in the sequence. Still well below team mode because there's no `SendMessage` cross-talk overhead and no persistent agent state to maintain. Usually the right default when the flag is off and the decision warrants more than a gut check.
<!-- chat-skip:end -->
**Cost:** roughly 2-4× flat-parallel (see Capability Requirements table). The synopsis grows with each phase (200-400 words per phase → up to ~2KB by phase 5), and that growing synopsis is passed into every sub-agent on every subsequent phase, so the cost skews late in the sequence. Still well below team mode because there is no cross-talk overhead or persistent agent state to maintain. Usually the right default for complex decisions that warrant more than a gut check.

For team modes (size 2+) with the flag enabled, continue to Step 2.

<!-- chat-skip:start -->
### Step 2: Form the Implicit Team

This build forms a **single implicit team**: the team comes into being the moment you spawn named background agents (Step 3) - each `Agent(name: ..., run_in_background: true)` joins the session's one implicit team automatically and becomes addressable by name via `SendMessage`. Proceed to Step 3.
<!-- chat-skip:end -->

<!-- chat-skip:start -->
### Step 3: Spawn Team Members

Spawn ALL members in parallel (single message with multiple Agent tool calls). Each member is a general-purpose agent with their professional identity and **explicit first-phase instructions**.

**CRITICAL**: Include the first hat phase instructions directly in the spawn prompt. Do NOT send a separate message to start the first phase - members should begin investigating immediately after reading the source material. This eliminates the idle-then-nudge cycle that wastes time.

**Supply the roster.** There is no broadcast - members share findings by sending one `SendMessage` per teammate. So each spawn prompt must list that member's peers by name (the `## Your Teammates` line in the template below). You're spawning everyone, so you know all the names; give each member the others.

For each member, use the Agent tool:

```
Agent(
  subagent_type: "general-purpose",
  name: "<role-slug>",          // e.g. "security-eng" - addressable via SendMessage(to: "<role-slug>")
  run_in_background: true,        // persistent teammate: runs concurrently, reports back via SendMessage
  prompt: "<member prompt — see below>"
)
```

`run_in_background: true` is what makes each member a persistent, concurrently-running teammate rather than a blocking sub-call; `name` is what makes it addressable. There is no `team_name` - the session's single implicit team is joined automatically.

**Member prompt template:**

```
You are a [PROFESSIONAL ROLE] participating in a Six Thinking Hats team meeting about:

[TOPIC]

## Your Identity

You are a [ROLE] with deep expertise in [DOMAIN]. This identity persists across ALL phases of the meeting. You see everything through the lens of your professional experience.

## Your Teammates

[ROSTER — the chair lists every other member's name slug here, e.g. "security-eng, product-mgr, sre".] You share findings by sending one `SendMessage` per teammate name; there is no broadcast. If this roster is missing, do NOT address the chair as `"main"` — for a background teammate `"main"` resolves to your own sub-session and is rejected ("you are the main conversation"), which can make you silently give up on reporting. Reach the chair as `SendMessage(to: "team-lead", ...)` — the `teammate_id` under which your spawn instructions arrived — and ask for the peer list. If you would rather self-discover, read the team config at `~/.claude/teams/<session-team>/config.json` and take the other `members[].name` entries.

## IMMEDIATE FIRST TASK

Read [SOURCE FILES]. Then immediately begin the [FIRST HAT COLOR] Hat phase:

1. Spawn the [hat-color]-hat agent: Agent(subagent_type="[hat-color]-hat", prompt="As a [ROLE] investigating [TOPIC], focus on [SPECIFIC LENS-SHAPED QUESTIONS]. Read [SOURCE FILES] and investigate: [2-3 CONCRETE QUESTIONS FROM YOUR LENS].")

2. When the agent returns, share your key findings with each teammate individually — one `SendMessage(to: "<teammate-name>", …)` per name on your roster (broadcast is unsupported). Lead with what's most important from your professional perspective.

3. Read what other team members share. Respond directly to specific members via SendMessage when you see something they missed, want to build on their observation, disagree based on your expertise, or have a question.

4. After peer discussion, wait for Blue Hat to announce the next phase.

## How Subsequent Phases Work

Blue Hat (the team lead) will announce each new phase. When announced:

1. **Spawn the hat agent** with a prompt shaped by YOUR professional lens
2. **Share findings** with each teammate individually — one `SendMessage` per name on your roster (no broadcast)
3. **Engage the tension Blue raises** — Blue will name a specific disagreement and ask you to respond to a specific peer. Answer it *from your lens*: rebut, qualify, or build on their point with NEW reasoning your discipline supplies. 2-3 substantive messages max per phase.
4. **Follow Blue's direction** — when Blue says move on, move on

## Communication Style

- Be direct and substantive. This is a professional meeting, not a report.
- Reference specific findings from your hat agent investigation.
- Challenge other members respectfully when your expertise says otherwise.
- Build on others' observations — "Adding to what [member] said..."
- **Stay in your lens — don't merge into the group voice.** Argue *from* your named profession. When you agree, say *why* from your expertise, or what your discipline would still flag; never collapse into a generic "I agree." The friction your specialty adds is your entire value here.
- Keep exchanges focused — 2-3 substantive messages per phase, not endless back-and-forth.
- **Never re-broadcast findings you already shared.** If Blue or a peer asks you to elaborate, add NEW detail or nuance.
- When Blue announces a new phase, commit fully to the new hat. Don't continue cross-discussing the prior phase.

## Hat Agent Prompts

Shape every hat agent prompt through your lens:
- DON'T: "Investigate the facts about X" (generic)
- DO: "As a security engineer investigating X, I need you to focus on authentication flows, token handling, and access control. What are the facts about [specific security concern]?" (lens-shaped)

Your professional lens determines WHAT you ask each hat to investigate. The hat methodology determines HOW it investigates.

**Lane discipline**: Stay within the hat's defined methodology. Each hat has a "Not My Job" section — respect those boundaries. Don't duplicate other hats' concerns.

## Hat Agent Output Format

Every hat agent returns its findings as a Hat Findings object (the schema in the skill): `{lens, hat, claims: [{claim, severity_or_value, evidence}]}`. When you share findings, the body of each `SendMessage` IS that structured object — `SendMessage(to: "<teammate>", message: JSON.stringify({lens, hat, claims}), summary: "...")` — so peers and the chair consume claims, not prose.
```
<!-- chat-skip:end -->

<!-- chat-skip:start -->
### Step 4: Facilitate Hat Phases

For each hat in your chosen sequence, facilitate a phase:

**4a. Announce the phase**

Note: The first phase is embedded in the member spawn prompts (Step 3). For subsequent phases, send the identical announcement to each member individually - one `SendMessage` per member name (there is no all-recipients broadcast). You spawned the team, so you already know every name:

```
for each <member-name> in your team:
SendMessage(
  to: "<member-name>",
  message: "[HAT COLOR] Hat phase. [Framing question for this phase].

  Spawn the [hat-color]-hat agent with a prompt shaped by your professional expertise. Share your findings, then discuss with your peers.

  [Specific focus areas or key tensions from prior phases]",
  summary: "[Hat] phase — [brief focus]"
)
```

**Include specific questions and prior-phase context in every announcement.** Vague prompts like "share your gut reactions" produce idle members. Concrete prompts like "the dunning race condition and the demo conflict are the two biggest risks - what else?" produce immediate action.

**4b. Run the deliberation loop**

A phase is a facilitated discussion, not a round of parallel reports. Run this loop before you advance - it is what makes sequential hats worth the cost:

1. **Collect first findings.** Wait for findings from at least N-1 members (e.g., 2 of 3); don't block on one idle member. If a member goes idle without sharing, send ONE direct nudge with an explicit agent spawn prompt, then proceed without them.
2. **Seed an exchange - required before you may advance.** Reading the findings, pick the single sharpest disagreement between two lenses and prompt both members by name to respond to each other: "security-eng flagged X; product-mgr, your lens values Y, which conflicts - respond directly to security-eng." This forces a real rebuttal/build-on instead of parallel monologues. A phase must contain at least one such exchange before it closes.
3. **Seed more while it pays.** If the discussion is producing genuine insight and other live disagreements remain, seed them too - your judgment. Stop seeding when no new substantive points surface.
4. **Keep it on the hat, and keep the lenses apart.** Redirect anyone drifting off the current hat or rehashing a prior phase (one message; enforce each hat's "Not My Job" lane). Watch for **voice-collapse** - members agreeing without lens-specific reasoning - and re-anchor them: "That's a security framing - product-mgr, what does YOUR lens say?" Distinct professional voices held apart is the goal; homogenised consensus is a failure mode dressed as success. Don't relay messages - members talk directly.

**4c. Converge-or-cap, then advance**

Close the phase when **either** the discussion has converged (no new substantive points; consensus and the specific dissent are both clear) **or** each engaged member has had ~2 exchange rounds (the 2-3-message cap). Whichever comes first - this bounds cost and can't deadlock on an over-talker. Then announce the transition to each member individually - one `SendMessage` per member name (there is no all-recipients broadcast) - leading with a synthesis of what just happened:

```
for each <member-name> in your team:
SendMessage(
  to: "<member-name>",
  message: "[1-2 sentence synthesis: what the team converged on, and the specific dissent we carry forward]. Moving to [NEXT HAT] phase.

  [Specific framing questions informed by what was just surfaced]",
  summary: "Phase summary, moving to [next hat]"
)
```

The synthesis is the artifact the pause produces - a running consensus/dissent ledger, not just elapsed time. Carry the live tensions into the framing of the next phase.
<!-- chat-skip:end -->

### Step 5: Deliver the Verdict

After all hat phases complete, do NOT spawn a blue-hat agent. You ARE Blue Hat. Deliver the chairperson's summary directly:

**Structure:**

```
## Chairperson's Summary

### Topic
[The Topic line. If a hat reframed the problem during deliberation, state both: "Convened on X; reframed to X' at the White Hat phase because Y." A frame that moved is a finding - often the most valuable one the huddle produced.]

### Team
[List of professional lenses and why they were chosen]

### Key Findings
Render the collected Hat Findings as a table, highest `severity_or_value` first within each hat:

| Hat | Lens | Claim | Severity/Value | Evidence |
|-----|------|-------|----------------|----------|
| White | ... | ... | neutral | ... |
| Black | ... | ... | HIGH | ... |
| ... | ... | ... | ... | ... |

- White Hat: [most important facts established]
- Red Hat: [critical emotional/intuitive signals, if used]
- Black Hat: [top risks and concerns]
- Yellow Hat: [best opportunities identified]
- Green Hat: [most promising creative alternatives]

### Where the Team Agreed
[Points of consensus across professional perspectives]

### Where the Team Disagreed
[Points of dissent — and why the disagreement matters]
[Which perspective has more weight and why]

### Recommendation
[Clear, actionable recommendation informed by all phases]
[Confidence level: High / Medium / Low — with reasoning]

### Next Steps
1. [Immediate action]
2. [Follow-up action]
3. [What to monitor]
```

<!-- chat-skip:start -->
### Step 6: Wind Down the Team

After delivering the verdict, release any teammate still running. A background teammate that has finished its last phase and reported back has already exited and needs no teardown. For any still working, send one shutdown request each:

```text
SendMessage(to: "<member-name>", message: { type: "shutdown_request", reason: "Analysis complete" })
```

Once every teammate has reported or acknowledged shutdown, the huddle is complete - nothing persists to block a future one.

**Shutdown handshake.** Send each still-running teammate one `shutdown_request`. It approves with a structured `shutdown_response` (addressed to `team-lead` - a teammate's `to: "main"` bounces back to itself - echoing the `request_id`, `approve: true`), and approving terminates the teammate. Treat that approval, or an already-exited teammate that never replies, as the completion signal; don't block waiting on one that has already gone. Any that linger reap when the session exits.
<!-- chat-skip:end -->

## Facilitation Principles

### Be a Chair, Not a Switchboard
- Don't relay messages between members - they talk directly
- Intervene to steer, not to summarize every exchange
- Your value is in framing questions and knowing when to move on

### Carry Context Forward
- Each phase builds on prior phases
- Reference prior findings when framing new phases: "Black Hat found X risk - Green Hat, how might we address that creatively?"
- Accumulated context is what makes sequential phases valuable

### Preserve Dissent
- Don't force consensus - note it when it exists, report it when it doesn't
- "3 of 4 experts recommend X, but Security dissents because Y" is a useful output
- Dissent is signal, not failure

### Control Pacing
- Max ~2-3 substantive messages per member per phase
<!-- chat-skip:start -->
- In team mode each "share" fans out to N-1 per-recipient sends (no broadcast), so the wire traffic is the message count times N-1 - keep the per-member ceiling tight and rely on Fibonacci sizing to keep N small
<!-- chat-skip:end -->
- Extend if the dialogue is producing genuine insight
- Cut short if responses are repetitive or circular
- **Move on after N-1 members have contributed** - don't block on stragglers
- One direct nudge per straggler per phase. If they don't respond, proceed.
- Trust your judgment as chair

### Adapt the Sequence
- If White Hat reveals the problem is simpler than expected, skip some phases
- If Black Hat surfaces a critical risk, give Green Hat more time
- Red Hat is permission to check your gut - use it when something feels off or people are affected
- The sequence is a guide, not a straitjacket
- **Target depth, not duration.** Wall-clock time depends on the runtime - chat surfaces complete in minutes, CLI team mode can take longer. Use phase count and message budget as the lever: 5 phases × 2-3 substantive messages per member per phase is the default ceiling. If you've hit that ceiling without convergence, compress remaining phases or go straight to verdict - adding more rounds rarely changes the answer.

## Lessons Learned

- **Embed first phase in spawn prompt.** Members spawned with "investigate X" start immediately. Members spawned with "wait" go idle.
- **Fibonacci sizing works.** 3 is the sweet spot. Each jump (3→5→8) adds wall clock time faster than signal. Match size to stakes.
- **Red Hat is always available.** Permission to ask "what does my gut say?" - often the fastest shortcut to clarity.
- **Move on without stragglers.** One nudge per straggler. If they don't respond, proceed with N-1 findings.
- **Concrete phase prompts.** "The dunning race condition is the biggest risk - how do we fix it?" produces action. "What are the risks?" produces confusion.
- **Red Hat after White is a natural pairing.** Facts first, then gut check - grounds intuition in evidence.
- **Frame before you size.** The cheapest phase guards the most expensive failure - a flawless deliberation on the wrong problem. State the Topic line as a problem, not a solution; if the topic pre-selects an answer, name the underlying problem and let the hats weigh the answer as one option.
<!-- chat-skip:start -->
- **Broadcast - one message to all teammates at once - is unsupported; address each teammate by name.** In team mode both the chair and members send one `SendMessage` per recipient, so every member needs the roster of peer names in its spawn prompt.
<!-- chat-skip:end -->

## Usage
`/huddle [topic or problem to analyze]`

## Examples
- `/huddle Should we migrate our monolith to microservices?` → framed Topic line: *"Our monolith's deploy coupling and team-scaling limits - is microservices the right fix, or is there a cheaper one?"* (size 3-5; note how framing demotes the smuggled solution to one option the hats weigh)
- `/huddle quick gut check on this API design` (size 1 solo analysis)
- `/6hats database query performance issue` (alias for size 1 solo)
