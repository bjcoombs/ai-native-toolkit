# Contributing

How to set up a clone, run the checks and open a pull request. The detailed rules live in [`docs/contributing/`](docs/contributing/), and [`CLAUDE.md`](CLAUDE.md) holds the in-repo contract that agents and people editing this repository both follow. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report a vulnerability privately, as [SECURITY.md](SECURITY.md) describes, never in a public issue.

## Set up a clone

You need [uv](https://docs.astral.sh/uv/), `git`, `make` and [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`).

```bash
git clone https://github.com/bjcoombs/ai-native-toolkit.git
cd ai-native-toolkit
make setup    # sync the locked dev environments for skills/assess and scripts
```

To try your branch as an installed plugin in Claude Code, follow [Testing a branch locally](docs/testing-a-branch-locally.md). On Windows, or anywhere without `make`, [Windows setup](docs/windows.md) lists the command each target runs.

## Run the checks

```bash
make check            # every merge-gating suite, lint and type check
make test-assess      # skills/assess pytest, with its coverage floor
make test-scripts     # scripts/ pytest, with its coverage floor
make test-contract    # plugin contract pytest (tests/)
make lint             # ruff and mypy
make regen            # rewrite every generated file and golden
```

`make help` lists every target. If your global git config signs commits or runs hooks, prefix a run with `GIT_CONFIG_GLOBAL=/dev/null`: the test fixtures make their own commits. A green `make check` predicts green required checks; [CI and merge gates](docs/contributing/ci.md) names each check and the workflow behind it.

## Open a pull request

- **One issue per pull request.** Start from an issue that meets the [definition of ready](docs/contributing/definition-of-ready.md); the issue form asks for the same six sections. Write `Closes #<n>` in the body for the issue it resolves, and `Part of #<n>` for a parent issue that was split into smaller ones.
- **Conventional-commit title**, such as `feat: Add the --tour flag` or `fix: Restore the doc graph orphan count`. The `Validate PR title` check enforces it.
- **Fill in the template.** [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) carries the sections: Summary, Changes Made, Technical Details, Testing, Risk Assessment, Deployment Notes.
- **Bump the version in the same pull request.** `.claude-plugin/plugin.json` follows semver; the table in [`CLAUDE.md`](CLAUDE.md#versioning) says which part moves. While a version freeze holds, leave every counter as it is on `main` and add a change note instead, as [Version-freeze notes](docs/contributing/release.md#version-freeze-notes) describes.
- **Keep the guardrails.** Files under the ratchet stay under their line limit, coverage floors only rise, and the floor files need the maintainer's sign-off. [`CLAUDE.md`](CLAUDE.md#invariants) lists every invariant.

## Further reading

- [Goal](docs/contributing/goal.md): the goal every skill and report serves, and the test for any new feature.
- [CI and merge gates](docs/contributing/ci.md): workflows, branch protection, the type gate, the file-size ratchet and the coverage floor.
- [Definition of ready](docs/contributing/definition-of-ready.md): when an issue is ready for an agent, and the triage labels.
- [Releasing](docs/contributing/release.md): how a release is cut and how the [changelog](CHANGELOG.md) is kept.
- [/assess architecture](docs/contributing/assess-architecture.md) and [Standalone skill pipeline](docs/contributing/standalone-skills.md): how the deterministic core and the standalone ZIPs are built.
- [Marathon notes](docs/contributing/marathon.md): the reasoning behind the marathon settings in `CLAUDE.md`.
