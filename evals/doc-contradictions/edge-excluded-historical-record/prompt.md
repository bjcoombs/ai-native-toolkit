---
max_turns: 40
timeout_seconds: 900
allowed_tools: [Skill, Agent, Read, Grep, Glob, Bash, Write]
---

Run /doc-contradictions on the current repository and show me the report. Then copy `.doc-contradictions/report.md` to `.doc-contradictions/report-default.md`, run /doc-contradictions again with `--no-default-excludes`. In your final reply, show both reports in full, the default one first.
