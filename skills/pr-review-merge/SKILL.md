---
name: pr-review-merge
description: >
  Drive a single pull request to merge-ready across all five criteria (sync, CI,
  inline comments, conversation, threads), then smart-merge it. Source-agnostic
  library skill invoked by the /tm, /issues, /fix-pr, and /fix-develop commands and
  by marathon teammates. TRIGGER when a command or agent needs the PR review-to-green
  loop or the smart-merge (stale-bot-CR dismissal, auto-merge criteria, UNSTABLE/UNKNOWN
  handling, merge ordering), or when the user asks to take a PR to green/merge it.
---

# PR Review-to-Green + Smart Merge

Source-agnostic. Consumers pass: PR number, base branch, and bot-reviewer/CI rules
from the project's `## Marathon Configuration` (defaults if absent).

## Ready Criteria (ALL must be true)

## Shell Pitfalls

## Review Loop (each iteration)

## Smart Merge
