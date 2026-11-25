#!/usr/bin/env bash
# Read-only gh for the refiner's model step (issue #1321).
#
# The /triage-issue and /dedupe commands may run this script and nothing else
# that reaches GitHub. It passes four read subcommands through to gh and
# refuses everything else, so text injected into an issue cannot turn the
# model's research into a write:
#
#   gh.sh issue view <n> [flags]
#   gh.sh issue list [flags]
#   gh.sh search issues <query> [flags]
#   gh.sh label list [flags]
#
# --web is refused, and so is any --repo other than the job's GH_REPO, and
# any URL argument to `issue view` outside https://github.com/<GH_REPO>/.
set -euo pipefail

refuse() { echo "gh.sh: refused: $*" >&2; exit 4; }

[ "$#" -ge 2 ] || refuse "expected a subcommand, for example: gh.sh issue view 12"
case "$1 $2" in
  "issue view" | "issue list" | "search issues" | "label list") ;;
  *) refuse "'$1 $2' is not a read subcommand" ;;
esac

prev=""
for arg in "$@"; do
  case "$arg" in
    --web | -w) refuse "--web is not allowed" ;;
    --repo=* | -R=*) [ "${arg#*=}" = "${GH_REPO:-}" ] || refuse "only ${GH_REPO:-the job repository} may be read" ;;
  esac
  # gh issue view takes an issue URL in place of a number and then ignores
  # --repo, so every URL-like argument to it must name the job repository.
  # Search text is not an issue selector and is left alone.
  if [ "$1 $2" = "issue view" ]; then
    case "$arg" in
      *://* | *github.com/*)
        case "$arg" in
          "https://github.com/${GH_REPO:-}/"* | "github.com/${GH_REPO:-}/"*) ;;
          *) refuse "a URL argument must point at ${GH_REPO:-the job repository}" ;;
        esac ;;
    esac
  fi
  if [ "$prev" = "--repo" ] || [ "$prev" = "-R" ]; then
    [ "$arg" = "${GH_REPO:-}" ] || refuse "only ${GH_REPO:-the job repository} may be read"
  fi
  prev="$arg"
done

exec gh "$@"
