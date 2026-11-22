## Output: `agent_clusters.json`

Which teams are building the same agent? Written on every run from the
`agent_building` blocks in `repos.jsonl` (an unscored inventory, issue #689).
Logic: `skills/assess/scripts/lib/fleet_agent_building.py`; per-repository scan:
`skills/assess/scripts/lib/agent_building.py`. Not kept in the run history.

Each repository gets one class, first match wins:

1. `builds-agent`: an agent framework is declared in a dependency manifest
   (LangChain, LangGraph, the Claude Agent SDK, OpenAI Agents, CrewAI ...), or a
   model SDK is declared (Anthropic, OpenAI, Gemini ...) and either a source file
   matches a tool-call loop or the repository has the API-client pairing.
2. `builds-mcp-server`: a source file registers MCP server tools, resources or prompts.
3. `uses-agent-tooling`: agent configuration only: an agent hint file (`CLAUDE.md`,
   `AGENTS.md` ...), `.mcp.json`, `.claude/agents/`, or a CI job that runs an agent.
4. `none`: otherwise, including a model SDK with no tool-call loop and no pairing.

The pairing is an issue-tracker API client (Jira, Linear) and a Git-host API
client (GitHub, GitLab) in the same repository, from manifests or REST endpoints
in source: the marker of a ticket-to-merge-request agent. A Git host counts as its
own issue tracker when the source calls both its issues API and its merge-request
or pull-request API through that host's client (python-gitlab, PyGithub, Octokit,
go-gitlab and the like), so an agent that reads GitLab issues and opens merge
requests on GitLab has the pairing too; issues on one host and pull requests on
another do not. Test files, vendored
trees and untracked files are not evidence. SDKs, config kinds, trackers and
hosts are slugs from a pinned vocabulary, so counts-only keeps them.

Cluster rule: two `builds-agent` repositories are linked when they have the same
class, the Jaccard similarity of their SDK and framework sets is at least 0.5
(two empty sets are not similar), and they have the same pairing value (both
carry the issue-tracker plus Git-host marker, or neither does). Clusters are the
connected components of that relation; a single repository is not a cluster.
Deterministic and offline.

| Key | Value |
|-----|-------|
| `agent_clusters_version` | File version (1). |
| `rule` | The cluster rule in words. |
| `named` | `true` with `--name-agent-clusters`. |
| `evidence_repos` | Scanned repositories with an available `agent_building` block. |
| `builds_agent_repos`, `clustered_repos` | `builds-agent` repositories, and how many of them are in a cluster. |
| `clusters` | Largest first, then by SDK union and pairing (never by name): `id` (`c1`, `c2` ...), `agent_class`, `size`, `groups` (distinct groups spanned), `sdks` (union), `shared_sdks` (intersection), `pairing`. Only with `--name-agent-clusters`: `repo_ids` and `group_ids`. |

Without the naming flag no repository id or group name appears in the file, so a
cluster says that two teams build similar agents without saying which.
