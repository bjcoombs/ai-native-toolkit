#!/usr/bin/env python3
"""Self-anchor check for the floor (PRD E2): is the floor still enforced?

The floor markers and clause checks only bite while ``floor.yml`` is a *required*
status check and while ``floor.yml`` itself is path-restricted. A settings change
that drops either of those silently disarms the whole floor. This script closes
that gap: it queries the live GitHub API on every run and fails loudly if

  1. the floor status check is no longer required on the default branch, or
  2. the path restriction on ``.github/workflows/floor.yml`` is gone.

It fails CLOSED: any inability to confirm the anchor (missing token, insufficient
permissions, absent protection/ruleset) is a failure, never a pass. Reading
branch protection and rulesets requires admin:read, which the default Actions
``GITHUB_TOKEN`` does not carry -- provide a fine-grained PAT with
"Administration: read" as the ``FLOOR_ANCHOR_TOKEN`` secret. This out-of-band
maintainer step is itself part of the floor (clause iii).

Stdlib only.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

# The required status check context (the deterministic floor.yml job name) and
# the path the push ruleset must restrict.
FLOOR_CONTEXT = os.environ.get("FLOOR_CONTEXT", "floor enforcement")
# The anchor job's own context. It must ALSO be a required check: if only the
# deterministic layer is required, a later PR could drop it from protection, the
# anchor would go red without gating, and the floor would silently disarm (E2).
ANCHOR_CONTEXT = os.environ.get("ANCHOR_CONTEXT", "floor self-anchor")
FLOOR_PATH = ".github/workflows/floor.yml"


class AnchorError(Exception):
    """A settings gap or a failure to confirm the anchor. Always fails closed."""


def remediation(repo: str) -> str:
    """The three maintainer-only, out-of-band commands that arm the anchor.

    Named verbatim so the red is actionable without opening the proof doc. These
    require owner/admin and a fine-grained PAT with 'Administration: read' -- the
    default Actions GITHUB_TOKEN cannot read branch protection or rulesets, which
    is why the anchor fails closed until they are run (FLOOR.md clause iii).
    """
    return f"""\
Missing configuration (maintainer-only, run out-of-band -- see docs/floor-anchor-proof.md):

  1. Register BOTH floor checks as required on the default branch (preserving the
     four existing required contexts). Both floor contexts must be required, or a
     later PR could drop one and silently disarm that layer:

     gh api "repos/{repo}/branches/main/protection/required_status_checks" \\
       --method PATCH \\
       -f 'checks[][context]=skills/assess pytest' \\
       -f 'checks[][context]=scripts/ pytest' \\
       -f 'checks[][context]=plugin contract pytest' \\
       -f 'checks[][context]=Validate PR title' \\
       -f 'checks[][context]={FLOOR_CONTEXT}' \\
       -f 'checks[][context]={ANCHOR_CONTEXT}'

  2. Path-restrict {FLOOR_PATH} via an active push ruleset (maintainer bypass),
     so a self-merged PR cannot gut the workflow itself:

     gh api "repos/{repo}/rulesets" --method POST --input - <<'JSON'
     {{ "name": "floor-workflow-path-lock", "target": "push", "enforcement": "active",
        "bypass_actors": [{{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}}],
        "rules": [{{"type": "file_path_restriction",
                   "parameters": {{"restricted_file_paths": ["{FLOOR_PATH}"]}}}}] }}
     JSON

  3. Create the anchor read token so this check can query the settings above:

     gh secret set FLOOR_ANCHOR_TOKEN --repo "{repo}"   # paste a PAT: Administration: read"""


def _write_step_summary(reason: str, repo: str) -> None:
    """Surface the anchor-layer failure in the GitHub job summary if available.

    Makes it unambiguous at a glance that the *anchor* layer is red while the
    deterministic layer (a separate 'floor enforcement' job) is untouched.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    body = (
        "## Floor self-anchor: FAIL (fail-closed by design, PRD E2)\n\n"
        f"**Anchor layer** could not confirm the floor is still enforced:\n\n"
        f"> {reason}\n\n"
        "The **deterministic layer** (marker-removal + FLOOR.md integrity) runs "
        "as the separate `floor enforcement` job and is unaffected by this "
        "failure.\n\n"
        "```\n" + remediation(repo) + "\n```\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        pass  # step summary is best-effort; the stderr message still fails the job


def _get(path: str, token: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "floor-anchor-check",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (fixed api host)
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def _default_branch(repo: str, token: str) -> str:
    status, data = _get(f"/repos/{repo}", token)
    if status != 200 or not isinstance(data, dict):
        raise AnchorError(f"cannot read repo metadata (HTTP {status}): {data}")
    return data.get("default_branch", "main")


def check_required_check(repo: str, branch: str, token: str) -> None:
    """Fail unless BOTH floor contexts are required status checks on ``branch``.

    The deterministic ``FLOOR_CONTEXT`` job *and* the ``ANCHOR_CONTEXT`` job (this
    self-anchor check) must each be required. Verifying only the deterministic one
    leaves a disarm path: if the anchor job is not itself required, a later PR
    could drop ``FLOOR_CONTEXT`` from protection -- the anchor would go red but no
    longer gate, and the floor would silently disarm (PRD E2). So we fail closed
    unless every floor context is present, naming exactly which one is missing.

    Checks both classic branch protection and repo rulesets, since either can
    supply a required check. A permission error (admin:read missing) fails closed.
    """
    contexts: set[str] = set()

    status, data = _get(
        f"/repos/{repo}/branches/{branch}/protection/required_status_checks", token
    )
    if status in (401, 403):
        raise AnchorError(
            f"cannot read branch protection (HTTP {status}). The default "
            "GITHUB_TOKEN lacks admin:read; set the FLOOR_ANCHOR_TOKEN secret to "
            "a fine-grained PAT with 'Administration: read'."
        )
    if status == 200 and isinstance(data, dict):
        contexts.update(data.get("contexts", []) or [])
        for chk in data.get("checks", []) or []:
            if isinstance(chk, dict) and chk.get("context"):
                contexts.add(chk["context"])
    elif status != 404:
        raise AnchorError(f"unexpected branch-protection response (HTTP {status}): {data}")

    # Rulesets can also require checks (target: branch, required_status_checks rule).
    contexts.update(_ruleset_required_contexts(repo, token))

    # Both the deterministic floor job and this self-anchor job must gate. Report
    # the specific missing context(s) so the red is actionable per layer.
    required = (FLOOR_CONTEXT, ANCHOR_CONTEXT)
    missing = [ctx for ctx in required if ctx not in contexts]
    if missing:
        named = " and ".join(repr(ctx) for ctx in missing)
        raise AnchorError(
            f"floor status check(s) {named} NOT required on {branch!r}. Both the "
            f"deterministic floor check ({FLOOR_CONTEXT!r}) and the self-anchor "
            f"check ({ANCHOR_CONTEXT!r}) must be required, or that layer can be "
            f"silently disarmed. Required contexts seen: {sorted(contexts) or 'none'}. "
            "Restore it (maintainer out-of-band sign-off)."
        )
    print(
        f"ok   both floor status checks ({FLOOR_CONTEXT!r}, {ANCHOR_CONTEXT!r}) "
        f"are required on {branch!r}."
    )


def _ruleset_required_contexts(repo: str, token: str) -> set[str]:
    # Best-effort: rulesets are a *second* place a required check can live. If
    # the token cannot read them, don't fail here -- either branch protection
    # already supplies the floor context (this call was just supplementary), or
    # it doesn't and the membership check fails with the clearer "NOT required"
    # message. The hard ruleset-access failure is owned by check_path_restriction,
    # which genuinely cannot proceed without ruleset read.
    contexts: set[str] = set()
    status, rulesets = _get(f"/repos/{repo}/rulesets", token)
    if status != 200 or not isinstance(rulesets, list):
        return contexts
    for summary in rulesets:
        rid = summary.get("id")
        if rid is None:
            continue
        st, detail = _get(f"/repos/{repo}/rulesets/{rid}", token)
        if st != 200 or not isinstance(detail, dict):
            continue
        for rule in detail.get("rules", []) or []:
            if rule.get("type") == "required_status_checks":
                params = rule.get("parameters", {}) or {}
                for chk in params.get("required_status_checks", []) or []:
                    if chk.get("context"):
                        contexts.add(chk["context"])
    return contexts


def check_path_restriction(repo: str, token: str) -> None:
    """Fail unless a push ruleset restricts modifications to FLOOR_PATH."""
    status, rulesets = _get(f"/repos/{repo}/rulesets", token)
    if status in (401, 403):
        raise AnchorError(
            f"cannot read rulesets (HTTP {status}). Set FLOOR_ANCHOR_TOKEN to a "
            "PAT with 'Administration: read'."
        )
    if status != 200 or not isinstance(rulesets, list):
        raise AnchorError(f"cannot list rulesets (HTTP {status}): {rulesets}")

    for summary in rulesets:
        rid = summary.get("id")
        if rid is None:
            continue
        st, detail = _get(f"/repos/{repo}/rulesets/{rid}", token)
        if st != 200 or not isinstance(detail, dict):
            continue
        if detail.get("enforcement") != "active":
            continue
        for rule in detail.get("rules", []) or []:
            if rule.get("type") != "file_path_restriction":
                continue
            params = rule.get("parameters", {}) or {}
            paths = params.get("restricted_file_paths", []) or []
            if _path_matches(FLOOR_PATH, paths):
                print(
                    f"ok   floor.yml path restriction is active "
                    f"(ruleset {detail.get('name', rid)!r})."
                )
                return
    raise AnchorError(
        f"no active push ruleset restricts {FLOOR_PATH!r}. A self-merged PR could "
        "gut floor.yml itself. Restore the file-path restriction ruleset "
        "(maintainer out-of-band sign-off)."
    )


def _path_matches(target: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat == target or pat == FLOOR_PATH:
            return True
        # fnmatch-style globs used by GitHub path restrictions
        if fnmatch.fnmatch(target, pat):
            return True
    return False


def _fail(reason: str, repo: str) -> int:
    """Emit a crisp, complete, actionable failure and fail closed."""
    print(f"FAIL floor self-anchor: {reason}\n", file=sys.stderr)
    print(remediation(repo), file=sys.stderr)
    _write_step_summary(reason, repo)
    return 1


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("FAIL GITHUB_REPOSITORY is not set.", file=sys.stderr)
        return 1
    token = os.environ.get("FLOOR_ANCHOR_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return _fail(
            "no token available -- the FLOOR_ANCHOR_TOKEN secret is not set and "
            "no GITHUB_TOKEN was provided, so repo settings cannot be read.",
            repo,
        )
    try:
        branch = _default_branch(repo, token)
        check_required_check(repo, branch, token)
        check_path_restriction(repo, token)
    except AnchorError as exc:
        return _fail(str(exc), repo)
    print("\nSelf-anchor check passed: the floor is still enforced by repo settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
