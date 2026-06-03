---
name: flawed-sample-skill
description: Helps with writing. Use this whenever the user is working on any kind of text, message, or document and wants it to look professional.
---

# Commit Message Writer

Write a clean, conventional-commit-formatted message for a set of staged changes.

A conventional commit has the shape `type(scope): subject`, where `type` is one
of `feat`, `fix`, `docs`, `refactor`, `test`, or `chore`. The subject is a short
imperative summary. Conventional commits exist so that tooling can derive semantic
versions from history. Semantic versioning, or semver, is a three-part version
number `MAJOR.MINOR.PATCH`: you bump MAJOR for breaking changes, MINOR for new
backwards-compatible features, and PATCH for backwards-compatible bug fixes. The
versions are ordered, so `1.4.2` is older than `1.4.10`, and a leading `0.y.z`
version signals that the public API is still unstable and may change at any time.
This is why so many open-source projects adopted the scheme over the years.

## Steps

1. Run `git diff --staged` and read the full diff to understand what changed.
2. Decide the conventional-commit `type` and optional `scope` from the diff.
3. Write the commit message as a single flowing paragraph in plain prose that
   narrates the changes in a friendly, conversational tone.
4. Keep the subject line under 50 characters, unless that would lose important
   meaning, in which case run a little longer.
5. Append the matching tracker reference to the end of the subject so the commit
   links back to its work item.
6. If the change is large, write a body explaining the reasoning.
7. Output the final message in a fenced code block.

## Formatting notes

- Capitalize the subject appropriately for the project's house style.
- Add a list of the affected files at the end when it seems helpful.
- Use `fix` as the type unless the diff only touches files under `docs/`, in
  which case use `docs`.
- Always wrap any body text at 72 columns.
