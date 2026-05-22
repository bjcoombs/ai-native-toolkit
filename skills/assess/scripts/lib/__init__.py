"""Deterministic core modules for /assess.

Public surface:
    agent_instructions_grader: heuristic scoring of agent instruction files
                               (CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
                               .github/copilot-instructions.md)
    stats_diff:                compare current vs prior complexity stats
    wiki_writer:               render wiki MD files from templates
"""

__version__ = "0.1.0"
