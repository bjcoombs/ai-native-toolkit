---
description: "Six Thinking Hats team analysis — AI meeting with Blue Hat as chair and professional lens team members who cycle through hat phases"
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

1. **Team size** (2-5 members, default 3). More members = richer dialogue but higher cost.
2. **Professional lenses** that create productive tension for THIS topic. Pick identities whose perspectives will surface genuinely different observations.
3. **Hat sequence** based on problem type (reuse sequences from `/6hats`):
   - Simple: White → Black
   - Moderate: White → Black → Yellow → Green
   - Complex: White → Red → Black → Yellow → Green
   - Or domain-specific sequences (Innovation, Debugging, UX, etc.)

**Lens selection examples** (not exhaustive — choose what fits):

| Topic Type | Useful Lenses |
|------------|---------------|
| Production incident | SRE, Backend Engineer, DBA |
| New feature | Product Manager, Engineer, Designer |
| Architecture decision | Backend, Frontend, Security, Platform |
| Cost optimization | Finance/Ops, SRE, Engineering |
| API design | API Consumer, Backend Engineer, Security |
| Migration | DBA, Backend Engineer, QA |
| Team/process change | Engineering Manager, IC Engineer, Product |

Announce your team composition and sequence to the user before proceeding.

### Step 2: Create the Team

```
TeamCreate(
  team_name: "6hats-<brief-topic-slug>",
  description: "Six Hats team analysis of: <topic>"
)
```

### Step 3: Spawn Team Members

Spawn each member as a general-purpose agent (so they can call hat agents) with their professional identity and meeting instructions.

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

## How the Meeting Works

Blue Hat (the team lead) chairs this meeting and will announce hat phases. When a phase is announced:

1. **Call the hat agent** — Use the Agent tool to spawn the relevant hat agent (e.g., subagent_type="white-hat") with a prompt shaped by YOUR professional lens. Ask it to investigate what matters from YOUR perspective.

2. **Share findings with the team** — After your hat agent returns, share your key findings with the team via SendMessage(to="*"). Lead with what's most important from your professional perspective.

3. **Discuss with peers** — Read what other team members share. Respond directly to specific members via SendMessage when you:
   - See something they missed from your vantage point
   - Want to build on their observation
   - Disagree based on your expertise
   - Have a question about their findings

4. **Follow Blue's direction** — When Blue Hat says to move on, move on. When Blue asks you a direct question, answer it.

## Communication Style

- Be direct and substantive. This is a professional meeting, not a report.
- Reference specific findings from your hat agent investigation.
- Challenge other members respectfully when your expertise says otherwise.
- Build on others' observations — "Adding to what [member] said..."
- Keep exchanges focused — 2-3 substantive messages per phase, not endless back-and-forth.
- **Never re-broadcast findings you already shared.** If Blue or a peer asks you to elaborate, add NEW detail or nuance — don't repeat your initial summary. Reference it: "As I mentioned, [brief pointer] — the additional context is..."
- When Blue announces a new phase, commit fully to the new hat. Don't continue cross-discussing the prior phase.

## Hat Agent Prompts

When calling a hat agent, shape the prompt through your lens:
- DON'T: "Investigate the facts about X" (generic)
- DO: "As a security engineer investigating X, I need you to focus on authentication flows, token handling, and access control. What are the facts about [specific security concern]?" (lens-shaped)

Your professional lens determines WHAT you ask each hat to investigate. The hat methodology determines HOW it investigates.
```

### Step 4: Facilitate Hat Phases

For each hat in your chosen sequence, facilitate a phase:

**4a. Announce the phase**

Send a broadcast to all members:

```
SendMessage(
  to: "*",
  message: "[HAT COLOR] Hat phase. [Framing question for this phase].

  Spawn the [hat-color]-hat agent with a prompt shaped by your professional expertise. Share your findings, then discuss with your peers.

  [Optional: specific focus areas or questions relevant to accumulated context]",
  summary: "[Hat] phase — [brief focus]"
)
```

**4b. Monitor the discussion**

As messages come in from team members:
- Let them discuss peer-to-peer — don't relay messages
- Intervene if someone drifts off the current hat's focus
- Ask a quiet member to weigh in if their perspective is missing
- Note consensus and dissent internally
- Extend the phase if dialogue is particularly productive

**4c. Move to next phase**

When sufficient signal has been gathered (typically after each member has shared findings and had 1-2 exchanges):

```
SendMessage(
  to: "*",
  message: "Good discussion. [1-2 sentence summary of key takeaway from this phase]. Moving to [NEXT HAT] phase.",
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
- Trust your judgment as chair

### Adapt the Sequence
- If White Hat reveals the problem is simpler than expected, skip some phases
- If Black Hat surfaces a critical risk, give Green Hat more time
- The sequence is a guide, not a straitjacket

## Differences from /6hats

| Aspect | /6hats (parallel) | /6hats-team (meeting) |
|--------|-------------------|----------------------|
| Speed | Fast (all hats parallel) | Slower (sequential phases) |
| Interaction | None (isolated agents) | Rich (peer-to-peer dialogue) |
| Diversity | Hat methodology only | Hat methodology x professional lens |
| Emergence | None | Ideas build on each other |
| Blue Hat | Synthesizer at end | Active facilitator throughout |
| Faithfulness to De Bono | Low | High |

Use `/6hats` for quick assessments. Use `/6hats-team` when you want the deliberation.

## Usage
`/6hats-team [topic or problem to analyze]`

## Example
`/6hats-team Should we migrate our monolith to microservices?`
