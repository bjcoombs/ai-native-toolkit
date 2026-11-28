# The push-capability check and the no-contributions scan

The two checks Step 5 of the assess-pr skill runs before the batched Phase 2 question is framed.

## Contents

- The push-capability and redirect check
- The no-contributions scan

## The push-capability and redirect check

```bash
# Detect push capability. `gh` returns viewer fields for the current user.
# Push capability is derived from `viewerPermission` (one of ADMIN |
# MAINTAIN | WRITE | TRIAGE | READ | NONE) - GitHub's GraphQL Repository
# type has no `viewerCanPush` field, so don't request it (the CLI errors
# and the whole call returns empty, silently degrading every write-
# accessible repo to the "leave local" branch).
PUSH_INFO=$(gh repo view --json viewerPermission,viewerCanAdminister,nameWithOwner,isFork,owner,hasIssuesEnabled 2>/dev/null || true)
PERM=$(echo "$PUSH_INFO" | jq -r '.viewerPermission // empty' | tr -d '\r')
case "$PERM" in
  ADMIN|MAINTAIN|WRITE) CAN_PUSH=1 ;;
  *)                    CAN_PUSH=0 ;;
esac
# A clone of the user's own fork reports ADMIN, so CAN_PUSH alone cannot tell
# the upstream's maintainer from an outsider working in their fork.
IS_FORK=$(echo "$PUSH_INFO" | jq -r '.isFork // false' | tr -d '\r')
# Gates the read-only upstream-issues offer: no issues list, no upstream issues.
HAS_ISSUES=$(echo "$PUSH_INFO" | jq -r '.hasIssuesEnabled // false' | tr -d '\r')
# isFork says the repo has a parent, not whose fork it is: a collaborator with
# push access to someone else's fork also sees isFork=true and WRITE. The
# current repo counts as the user's own fork only when the viewer owns it.
VIEWER_LC=$(gh api user 2>/dev/null | jq -r '.login // empty' | tr -d '\r' | tr '[:upper:]' '[:lower:]')
OWNER_LC=$(echo "$PUSH_INFO" | jq -r '.owner.login // empty' | tr -d '\r' | tr '[:upper:]' '[:lower:]')
if [ "$IS_FORK" = true ] && [ -n "$VIEWER_LC" ] && [ "$VIEWER_LC" = "$OWNER_LC" ]; then
  IS_OWN_FORK=true
else
  IS_OWN_FORK=false
fi
# If the command failed (no remote, no gh, not a GitHub repo, unauthenticated),
# $PUSH_INFO is empty and $PERM stays empty - fall back to the local-branch
# flow with the reason. Never silently assume push works.

# Detect remote-to-gh redirect: GitHub follows transfers/renames, so gh may
# resolve a different owner/repo than the configured remote implies.
REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
if [ -n "$REMOTE_URL" ]; then
  # Normalise SSH and HTTPS forms (and GitHub Enterprise hosts) to owner/repo.
  # SSH:   git@host:owner/repo.git   -> owner/repo
  # HTTPS: https://host/owner/repo/  -> owner/repo
  # Strip protocol, any user@, the host (not just github.com), a trailing .git,
  # and a trailing slash - so a GHE host or a trailing slash can't masquerade as
  # a redirect.
  REMOTE_SLUG=$(echo "$REMOTE_URL" \
    | sed -e 's|^[a-zA-Z]*://||' -e 's|^[^@/]*@||' -e 's|^[^/:]*[:/]||' -e 's|\.git$||' -e 's|/$||')
else
  REMOTE_SLUG=""
fi
GH_SLUG=$(echo "$PUSH_INFO" | jq -r '.nameWithOwner // empty' | tr -d '\r')
# Compare case-insensitively: GitHub treats owner/repo as case-insensitive, so a
# pure case difference is not a redirect and must not emit a spurious notice.
REMOTE_SLUG_LC=$(echo "$REMOTE_SLUG" | tr '[:upper:]' '[:lower:]')
GH_SLUG_LC=$(echo "$GH_SLUG" | tr '[:upper:]' '[:lower:]')
if [ -n "$REMOTE_SLUG" ] && [ -n "$GH_SLUG" ] && [ "$REMOTE_SLUG_LC" != "$GH_SLUG_LC" ]; then
  REDIRECT_NOTICE="Note: origin (\`$REMOTE_SLUG\`) redirects to \`$GH_SLUG\`; the offers below target the redirected repo."
else
  REDIRECT_NOTICE=""
fi
```

## The no-contributions scan

Then check whether the target turns away outside contributions. A fork PR against a repo whose README or CONTRIBUTING says "do not send a pull request" is noise for its maintainers, so the scan runs before any upstream offer is framed:

```bash
# no-contributions scan: start
# Sets NO_CONTRIBUTIONS (1/0) plus the first matching file (relative to the
# repo root) and line, so the offer can quote the statement back to the user.
NO_CONTRIBUTIONS=0
NO_CONTRIBUTIONS_SOURCE=""
NO_CONTRIBUTIONS_STATEMENT=""
# The pattern is built from named parts so every branch shares one noun and one
# boundary. _nc_noun ends at a non-letter (_nc_end), so "prs?" cannot match the
# start of "promise", "process" or "private"; the passive branch also needs a
# non-letter before the noun. _nc_neg has no leading boundary on purpose: it
# must match the tail of "cannot".
_nc_neg="(not|n't|n’t|unable to)"
_nc_noun="(contributions|pull requests?|prs?)"
_nc_end="([^a-z]|$)"
# Three refusal phrasings:
#   accept:     "does not accept (community) contributions", "not accepting PRs"
#   imperative: "do not send a pull request" - the clause must end right after
#               the noun, so "do not open a PR for typo fixes" (a house rule
#               for contributors, not a refusal) stays out
#   passive:    "pull requests are not accepted"
_nc_accept="${_nc_neg} ([a-z-]+ )?accept(ing)? ([a-z-]+ )?${_nc_noun}${_nc_end}"
_nc_imperative="${_nc_neg} (send|open|submit) (a |any |us )?${_nc_noun}[[:space:]]*([.;!)]|$)"
_nc_passive="(^|[^a-z])${_nc_noun} (are|is|will) not (be )?accepted"
_nc_re="${_nc_accept}|${_nc_imperative}|${_nc_passive}"
# Qualifiers: a matched line that carries one ("PRs are not accepted without an
# issue") is a condition on contributing, not a refusal, and is dropped. This
# errs towards 0, which keeps today's fork-to-upstream offer.
_nc_cond="(^|[^a-z])(${_nc_noun}|accepted) (without|until|unless|before|directly|against|that|which)"
for _nc_doc in "$REPO_ROOT/README.md" "$REPO_ROOT/CONTRIBUTING.md" "$REPO_ROOT/.github/CONTRIBUTING.md" "$REPO_ROOT/docs/CONTRIBUTING.md"; do
  [ -f "$_nc_doc" ] || continue
  _nc_line=$(grep -Ei -e "$_nc_re" "$_nc_doc" | grep -Eiv -e "$_nc_cond" | head -n 1)
  if [ -n "$_nc_line" ]; then
    NO_CONTRIBUTIONS=1
    NO_CONTRIBUTIONS_SOURCE=${_nc_doc#"$REPO_ROOT"/}
    NO_CONTRIBUTIONS_STATEMENT=$(printf '%s\n' "$_nc_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    break
  fi
done
# no-contributions scan: end
```
