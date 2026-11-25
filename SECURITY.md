# Security policy

## Report a vulnerability

Report it privately through GitHub: open the repository's [Security tab](https://github.com/bjcoombs/ai-native-toolkit/security) and choose **Report a vulnerability**, or go straight to the [advisory form](https://github.com/bjcoombs/ai-native-toolkit/security/advisories/new). Only the maintainer sees the report. GitHub's guide to the form: [Privately reporting a security vulnerability](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability).

Do not open a public issue, pull request or discussion for a vulnerability. Include the affected version (the `version` in `.claude-plugin/plugin.json`, or the release tag), the steps to reproduce it and what an attacker gains.

The fix ships in a new release, and the advisory is published once that release is out.

## Supported versions

Only the [latest release](https://github.com/bjcoombs/ai-native-toolkit/releases/latest) receives fixes. Update with `/plugin update ai-native-toolkit`, or download the new standalone skill ZIPs.

## Scope

In scope:

- The skills and agents under `skills/` and `agents/`, and the scripts they run, including the `/assess` deterministic core under `skills/assess/scripts/`.
- The standalone skill ZIPs published with each release.
- The GitHub Action (`action.yml`), the reusable workflows under `.github/workflows/` and the GitLab CI templates under `templates/`.

Examples: a script that runs code from the repository it scans, a counts-only artifact that leaks file text, a workflow that exposes a secret, or a skill instruction that makes an agent act outside the repository it was pointed at.

Out of scope:

- Vulnerabilities in Claude Code, the Claude apps or another AI assistant; report those to their vendor.
- Vulnerabilities in third-party tools the skills call (`gh`, `glab`, `uv`, `rg`, `jq`) or in Python dependencies, unless this repository uses them unsafely.
- A model following instructions that the person running it supplied.
