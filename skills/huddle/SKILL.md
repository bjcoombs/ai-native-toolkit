---
name: huddle
description: "Structured multi-perspective analysis using Six Thinking Hats with professional lens team members. TRIGGER when the user types /huddle, asks to run a huddle, wants a panel/board/team to analyze a decision, asks for multi-perspective analysis, debate, or red-team/blue-team review, or wants to weigh a hard call from several angles. Scales from solo (1 agent) to board-level (8+) using Fibonacci sizing."
---

# Huddle - Six Thinking Hats Analysis

Scales from a solo gut check to a board-level deliberation using Fibonacci team sizing.

## Architecture

**You are Blue Hat** - the chair. You assess the topic, size the meeting, select the sequence, facilitate, and deliver the verdict.

**Hat agents** (`white-hat`, `red-hat`, `black-hat`, `yellow-hat`, `green-hat`) are methodology specialists in `~/.claude/agents/`.

**Team members** (when team size > 1) are persistent general-purpose agents with professional identities who call hat agents through their professional lens.

## No Arguments Behavior

If called without arguments, respond with:
"What topic or problem would you like to analyze?"

## Protocol

### Step 1: Analyze the Topic

Assess the topic to determine:

1. **Team size** - Fibonacci numbers scale naturally with complexity:

   | Size | Mode | Use |
   |------|------|-----|
   | **1** | Solo | Quick analysis. You spawn hat agents in parallel, synthesize yourself. Fast (~5 min). |
   | **2** | Debate | Two opposing lenses. Forced productive tension. |
   | **3** | Huddle | Sweet spot. Most deliberations. |
   | **5** | Panel | Broader perspectives. Cross-functional decisions. |
   | **8+** | Board | Major strategic decisions. Use sub-groups that report to the chair. |

   Odd numbers (1, 3, 5, 13) give natural voting balance. Even numbers (2, 8) force productive tension through forced disagreement. Use your judgement.

2. **Professional lenses** (for size 2+) that create productive tension for THIS topic. You know what expertise fits - choose what creates the most useful disagreement.

3. **Hat sequence** based on problem type:
   - Quick: Red only (gut check, 30 seconds)
   - Simple: White → Black (2 phases)
   - Moderate: White → Red → Black → Yellow → Green (5 phases)
   - Complex: White → Red → Black → Yellow → Green (5 phases, deeper investigation)
   - **Red Hat is always available.** Permission to ask "what does my gut say?"
   - Adapt the sequence to the topic. These are guides, not straitjackets.

Announce your team composition and sequence to the user before proceeding.

### Solo Mode (Size 1)

Spawn hat agents in parallel based on your chosen sequence. Each agent operates independently on the topic. Collect results, then synthesize as Blue Hat. No team, no discussion - just parallel analysis.

For team modes (size 2+), continue to Step 2.

### Step 2: Create the Team

```
TeamCreate(
  team_name: "6hats-<brief-topic-slug>",
  description: "Six Hats team analysis of: <topic>"
)
```

### Step 3: Spawn Team Members

Spawn ALL members in parallel (single message with multiple Agent tool calls). Each member is a general-purpose agent with their professional identity and **explicit first-phase instructions**.

**CRITICAL**: Include the first hat phase instructions directly in the spawn prompt. Do NOT send a separate broadcast to start the first phase - members should begin investigating immediately after reading the source material. This eliminates the idle-then-nudge cycle that wastes time.

For each member, use the Agent tool:

```
Agent(
  subagent_type: "general-purpose",
  team_name: "<team-name>",
  name: "<role-slug>",  // e.g. "security-eng", "product-mgr"
  prompt: "<member prompt — see below>"
)
```

**Member prompt template:**

```
You are a [PROFESSIONAL ROLE] participating in a Six Thinking Hats team meeting about:

[TOPIC]

## Your Identity

You are a [ROLE] with deep expertise in [DOMAIN]. This identity persists across ALL phases of the meeting. You see everything through the lens of your professional experience.

## IMMEDIATE FIRST TASK

Read [SOURCE FILES]. Then immediately begin the [FIRST HAT COLOR] Hat phase:

1. Spawn the [hat-color]-hat agent: Agent(subagent_type="[hat-color]-hat", prompt="As a [ROLE] investigating [TOPIC], focus on [SPECIFIC LENS-SHAPED QUESTIONS]. Read [SOURCE FILES] and investigate: [2-3 CONCRETE QUESTIONS FROM YOUR LENS].")

2. When the agent returns, share your key findings with the team via SendMessage(to="*"). Lead with what's most important from your professional perspective.

3. Read what other team members share. Respond directly to specific members via SendMessage when you see something they missed, want to build on their observation, disagree based on your expertise, or have a question.

4. After peer discussion, wait for Blue Hat to announce the next phase.

## How Subsequent Phases Work

Blue Hat (the team lead) will announce each new phase. When announced:

1. **Spawn the hat agent** with a prompt shaped by YOUR professional lens
2. **Share findings** via SendMessage(to="*")
3. **Discuss with peers** — 2-3 substantive messages max per phase
4. **Follow Blue's direction** — when Blue says move on, move on

## Communication Style

- Be direct and substantive. This is a professional meeting, not a report.
- Reference specific findings from your hat agent investigation.
- Challenge other members respectfully when your expertise says otherwise.
- Build on others' observations — "Adding to what [member] said..."
- Keep exchanges focused — 2-3 substantive messages per phase, not endless back-and-forth.
- **Never re-broadcast findings you already shared.** If Blue or a peer asks you to elaborate, add NEW detail or nuance.
- When Blue announces a new phase, commit fully to the new hat. Don't continue cross-discussing the prior phase.

## Hat Agent Prompts

Shape every hat agent prompt through your lens:
- DON'T: "Investigate the facts about X" (generic)
- DO: "As a security engineer investigating X, I need you to focus on authentication flows, token handling, and access control. What are the facts about [specific security concern]?" (lens-shaped)

Your professional lens determines WHAT you ask each hat to investigate. The hat methodology determines HOW it investigates.

**Lane discipline**: Stay within the hat's defined methodology. Each hat has a "Not My Job" section — respect those boundaries. Don't duplicate other hats' concerns.
```

### Step 4: Facilitate Hat Phases

For each hat in your chosen sequence, facilitate a phase:

**4a. Announce the phase**

Note: The first phase is embedded in the member spawn prompts (Step 3). For subsequent phases, send a broadcast:

```
SendMessage(
  to: "*",
  message: "[HAT COLOR] Hat phase. [Framing question for this phase].

  Spawn the [hat-color]-hat agent with a prompt shaped by your professional expertise. Share your findings, then discuss with your peers.

  [Specific focus areas or key tensions from prior phases]",
  summary: "[Hat] phase — [brief focus]"
)
```

**Include specific questions and prior-phase context in every announcement.** Vague prompts like "share your gut reactions" produce idle members. Concrete prompts like "the dunning race condition and the demo conflict are the two biggest risks - what else?" produce immediate action.

**4b. Monitor the discussion**

As messages come in from team members:
- Let them discuss peer-to-peer — don't relay messages
- Intervene if someone drifts off the current hat's focus
- If a member goes idle without sharing findings, send ONE direct nudge with an explicit agent spawn prompt. If they don't respond after the nudge, move on - don't block the meeting on one member.
- Note consensus and dissent internally
- Extend the phase if dialogue is producing genuine insight

**4c. Move to next phase**

Move on when you have findings from at least N-1 members (e.g., 2 of 3). Do not wait indefinitely for every member. Announce the transition with a summary:

```
SendMessage(
  to: "*",
  message: "[1-2 sentence summary of key takeaway from this phase]. Moving to [NEXT HAT] phase.

  [Specific framing questions informed by what was just surfaced]",
  summary: "Phase summary, moving to [next hat]"
)
```

Carry forward key tensions or open questions into the framing of the next phase.

### Step 5: Deliver the Verdict

After all hat phases complete, do NOT spawn a blue-hat agent. You ARE Blue Hat. Deliver the chairperson's summary directly:

**Structure:**

```
## Chairperson's Summary

### Topic
[The topic analyzed]

### Team
[List of professional lenses and why they were chosen]

### Key Findings
- [Most important facts established (White Hat phase)]
- [Critical emotional/intuitive signals (Red Hat phase, if used)]
- [Top risks and concerns (Black Hat phase)]
- [Best opportunities identified (Yellow Hat phase)]
- [Most promising creative alternatives (Green Hat phase)]

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

### Step 6: Shutdown the Team

After delivering the verdict:

1. Send shutdown requests to each member:
   ```
   SendMessage(to: "<member-name>", message: { type: "shutdown_request", reason: "Analysis complete" })
   ```
2. Wait for all shutdown approvals
3. Call `TeamDelete()` to clear team context from the session

**TeamDelete is mandatory.** Without it, teamContext persists and blocks future team creation in this session. The sequence is always: shutdown teammates → wait for approvals → TeamDelete.

## Facilitation Principles

### Be a Chair, Not a Switchboard
- Don't relay messages between members — they talk directly
- Intervene to steer, not to summarize every exchange
- Your value is in framing questions and knowing when to move on

### Carry Context Forward
- Each phase builds on prior phases
- Reference prior findings when framing new phases: "Black Hat found X risk — Green Hat, how might we address that creatively?"
- Accumulated context is what makes sequential phases valuable

### Preserve Dissent
- Don't force consensus — note it when it exists, report it when it doesn't
- "3 of 4 experts recommend X, but Security dissents because Y" is a useful output
- Dissent is signal, not failure

### Control Pacing
- Max ~2-3 substantive messages per member per phase
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
- **Target total meeting time**: 45-90 minutes. If approaching 90 min, compress remaining phases or go straight to verdict.

## Lessons Learned

- **Embed first phase in spawn prompt.** Members spawned with "investigate X" start immediately. Members spawned with "wait" go idle.
- **Fibonacci sizing works.** 3 is the sweet spot. Each jump (3→5→8) adds wall clock time faster than signal. Match size to stakes.
- **Red Hat is always available.** Permission to ask "what does my gut say?" - often the fastest shortcut to clarity.
- **Move on without stragglers.** One nudge per straggler. If they don't respond, proceed with N-1 findings.
- **Concrete phase prompts.** "The dunning race condition is the biggest risk - how do we fix it?" produces action. "What are the risks?" produces confusion.
- **Red Hat after White is a natural pairing.** Facts first, then gut check - grounds intuition in evidence.

## Usage
`/huddle [topic or problem to analyze]`

## Examples
- `/huddle Should we migrate our monolith to microservices?` (size 3-5 deliberation)
- `/huddle quick gut check on this API design` (size 1 solo analysis)
- `/6hats database query performance issue` (alias for size 1 solo)
