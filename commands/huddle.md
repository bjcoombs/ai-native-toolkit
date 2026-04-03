---
description: "Huddle - structured multi-perspective analysis with professional lens team members who cycle through Six Thinking Hats phases"
argument-hint: "<topic or problem to analyze>"
---

# Six Thinking Hats: Team Meeting Mode

A faithful implementation of De Bono's Six Thinking Hats using Team Mode. Blue Hat chairs a meeting of professional experts who cycle through hat phases together, discussing peer-to-peer and calling hat agents for deep investigation.

## Architecture

```
You (Blue Hat — Chair / Team Lead)
  └── Team Members (persistent professional lenses)
        └── Hat Agents (called by members during each phase)
```

**You are Blue Hat.** You chair the meeting, select the team composition, facilitate each phase, steer discussion, and deliver the verdict.

**Team members** are persistent general-purpose agents with professional identities. They stay for the entire meeting and cycle through all hat phases. They can call hat agents (`white-hat`, `red-hat`, `black-hat`, `yellow-hat`, `green-hat`) as subagents for deep investigation.

**Hat agents** are the existing methodology specialists in `~/.claude/agents/`. Team members invoke them during the relevant phase to apply that hat's thinking methodology through their professional lens.

## No Arguments Behavior

If called without arguments, respond with:
"What topic or problem would you like the team to analyze?"

## Protocol

### Step 1: Analyze the Topic

Assess the topic to determine:

1. **Team size**: Odd numbers (3, 5) give natural voting balance for dissent. 3 is the sweet spot - a 5th only adds value when perspectives are truly orthogonal. More members = longer phases and more nudging required. Use your judgement.
2. **Professional lenses** that create productive tension for THIS topic. Pick identities whose perspectives will surface genuinely different observations. Avoid overlapping lenses. You know what expertise fits - choose what creates the most useful disagreement.
3. **Hat sequence** based on problem type:
   - Simple: White → Black (2 phases, ~20 min)
   - Moderate: White → Red → Black → Yellow → Green (5 phases, ~50 min)
   - Complex: White → Red → Black → Yellow → Green (5 phases, ~70 min)
   - **Red Hat is always available.** It gives permission to ask "what does my gut say?" - often the fastest shortcut to clarity. Consider it especially when there are human actors affected, or when something feels off but you can't articulate why yet.
   - **Target 45-90 minutes total.** If it's taking longer, reduce phases or team size.

Announce your team composition and sequence to the user before proceeding.

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

After delivering the verdict, gracefully shut down all team members:

```
SendMessage(
  to: "<member-name>",
  message: { type: "shutdown_request", reason: "Analysis complete" }
)
```

Do this for each member.

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

## Differences from /6hats

| Aspect | /6hats (parallel) | /huddle (meeting) |
|--------|-------------------|----------------------|
| Speed | Fast (~5 min) | 45-90 min target |
| Interaction | None (isolated agents) | Rich (peer-to-peer dialogue) |
| Diversity | Hat methodology only | Hat methodology x professional lens |
| Emergence | None | Ideas build on each other |
| Blue Hat | Synthesizer at end | Active facilitator throughout |
| Faithfulness to De Bono | Low | High |

Use `/6hats` for quick assessments. Use `/huddle` when the topic warrants deliberation and you have 60-90 minutes.

## Lessons Learned

- **Embed first phase in spawn prompt.** Members who are spawned with "read the material and wait" go idle. Members who are spawned with "read the material and immediately investigate X" start working.
- **Odd numbers for voting balance.** 3 is the sweet spot. 5 only when perspectives are truly orthogonal - each additional member adds wall clock time faster than signal.
- **Don't skip Red Hat reflexively.** It's permission to ask "what does my gut say?" - often the fastest shortcut to clarity. Especially valuable when people are affected or something feels off.
- **Move on without stragglers.** One nudge per straggler. If they don't respond, proceed with N-1 findings. Don't block the meeting.
- **Concrete phase prompts, not vague ones.** "What are the risks?" produces confusion. "The dunning race condition and demo conflict are the two biggest risks from Black Hat - how do we fix them?" produces action.
- **Red Hat after White is a natural pairing.** Facts first, then gut check - grounds intuition in evidence. But the chair decides what fits the topic.

## Usage
`/huddle [topic or problem to analyze]`

## Example
`/huddle Should we migrate our monolith to microservices?`
