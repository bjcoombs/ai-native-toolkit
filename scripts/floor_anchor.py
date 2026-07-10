#!/usr/bin/env python3
"""Self-anchor check for the floor (PRD E2): is the floor still enforced?

The floor markers and clause checks only bite while ``floor.yml`` is a *required*
status check. A settings change that drops it silently disarms the whole floor.
This script closes that gap: it queries the live GitHub API on every run and
HARD-FAILS (fail-closed) unless BOTH of these hold:

  1. both floor status checks (``floor enforcement`` and ``floor self-anchor``)
     are still required on the default branch, and
  2. branch protection is readable at all -- i.e. an anchor token with
     admin:read is present, so requirement 1 can actually be confirmed.

These two are fail-CLOSED: any inability to confirm them (missing token,
insufficient permissions) is a failure, never a pass. Reading branch protection
and rulesets requires admin:read, which the default Actions ``GITHUB_TOKEN``
does not carry -- provide a fine-grained PAT with "Administration: read" as the
``FLOOR_ANCHOR_TOKEN`` secret. This out-of-band maintainer step is itself part of
the floor (clause iii).

The floor.yml *path lock* (a push ruleset with ``file_path_restriction``) that a
third check once required is DESCOPED -- see ``PATH_LOCK_DESCOPED`` below. GitHub
refuses push rulesets on public, user-owned repos, so it is a documented
capability gap that this script WARNS about (loudly, non-failing) rather than
enforcing. The two requirements above remain fail-closed.

Stdlib only.
"""
from __future__ import annotations

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

# --- E2 DESCOPE: floor.yml path lock (maintainer decision, 2026-07-10) --------
# The path lock was a third hard requirement: an active push ruleset with a
# ``file_path_restriction`` rule over FLOOR_PATH, so a self-merged PR could not
# gut floor.yml itself. It is DESCOPED because GitHub structurally refuses push
# rulesets on public, user-owned repos. Observed evidence creating the ruleset
# (docs/floor-anchor-proof.md, Attack B):
#
#     HTTP 422
#     "Source public repos cannot have push rules"
#     "Source only org-owned repos can have push rules"
#
# Per PRD E2 the maintainer chose (2026-07-10) to DESCOPE the path lock rather
# than migrate the repo into an organization. This flag keys the descope on an
# explicit, cited decision -- not a silent removal -- so the gap stays legible.
# The self-anchor therefore treats the missing path restriction as a DOCUMENTED
# capability gap: it WARNS loudly (stderr + job summary) instead of failing. The
# two hard requirements (both floor checks required + branch protection readable)
# stay fail-closed.
PATH_LOCK_DESCOPED = True
PATH_LOCK_DESCOPE_DATE = "2026-07-10"
PATH_LOCK_DESCOPE_EVIDENCE = (
    'GitHub HTTP 422 creating the push ruleset: "Source public repos cannot '
    'have push rules" / "Source only org-owned repos can have push rules".'
)


class AnchorError(Exception):
    """A settings gap or a failure to confirm the anchor. Always fails closed."""


def remediation(repo: str) -> str:
    """The two maintainer-only, out-of-band commands that arm the anchor.

    Named verbatim so the red is actionable without opening the proof doc. These
    require owner/admin and a fine-grained PAT with 'Administration: read' -- the
    default Actions GITHUB_TOKEN cannot read branch protection, which is why the
    anchor fails closed until they are run (FLOOR.md clause iii). The floor.yml
    path lock is intentionally absent: it is DESCOPED (org-only GitHub feature,
    HTTP 422 on this public user-owned repo -- see ``PATH_LOCK_DESCOPED``).
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

  2. Create the anchor read token so this check can query the settings above:

     gh secret set FLOOR_ANCHOR_TOKEN --repo "{repo}"   # paste a PAT: Administration: read

Note: the floor.yml path lock (a push ruleset with file_path_restriction over
{FLOOR_PATH}) is DESCOPED per the maintainer's PRD-E2 decision \
({PATH_LOCK_DESCOPE_DATE}). {PATH_LOCK_DESCOPE_EVIDENCE} It is a documented
capability gap, not a required setting -- the self-anchor warns about it rather
than failing. See docs/floor-anchor-proof.md (Honest-degrade note, E2)."""


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
    # message. (The former path-lock check owned a hard ruleset-access failure;
    # it is now descoped -- see PATH_LOCK_DESCOPED -- so this is the only ruleset
    # read left, and it stays best-effort.)
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


def _descope_warning() -> str:
    """The loud, non-failing warning for the DESCOPED floor.yml path lock (E2).

    States the descope decision, its date, the 422 evidence, and the honest
    residual risk -- so the capability gap reads as a named, owned decision, not
    a silent hole.
    """
    return (
        f"floor.yml PATH LOCK is DESCOPED (maintainer PRD-E2 decision, "
        f"{PATH_LOCK_DESCOPE_DATE}). {PATH_LOCK_DESCOPE_EVIDENCE} The push "
        f"ruleset that would stop a self-merged PR from gutting {FLOOR_PATH} "
        "cannot be created on this public, user-owned repo, so it is a "
        "DOCUMENTED capability gap, not a job failure. Residual risk: a "
        "floor.yml-gut attack is detected only until a gutted workflow merges; "
        "prevention now rests on the required status checks (floor enforcement "
        "+ floor self-anchor), code review, and the retro boundary -- process "
        "signals, named as such. See docs/floor-anchor-proof.md (Honest-degrade "
        "note, E2)."
    )


def _write_descope_summary(message: str) -> None:
    """Surface the descope warning in the GitHub job summary if available.

    A warning, not a failure: the job still passes. Best-effort, like the FAIL
    summary writer -- the stderr ::warning:: annotation carries it regardless.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    body = (
        "## Floor self-anchor: floor.yml path lock DESCOPED (PRD E2)\n\n"
        "Documented capability gap -- a **warning, not a failure**. The two hard "
        "requirements (both floor checks required + branch protection readable) "
        "still gate this job fail-closed.\n\n"
        f"> {message}\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        pass  # step summary is best-effort; the ::warning:: annotation still shows


def warn_path_lock_descoped() -> None:
    """Emit the descope warning loudly (stderr + job summary) WITHOUT failing.

    Replaces the former ``check_path_restriction`` hard check. The path lock is a
    documented capability gap (``PATH_LOCK_DESCOPED``), so it is surfaced, not
    enforced. Returns normally so the anchor job stays green on its two hard,
    still-fail-closed requirements.
    """
    message = _descope_warning()
    # A GitHub Actions ::warning:: annotation on stderr so it is loud in the log.
    print(f"::warning::floor self-anchor: {message}", file=sys.stderr)
    _write_descope_summary(message)


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
    except AnchorError as exc:
        return _fail(str(exc), repo)
    # E2 descope: the floor.yml path lock is a documented capability gap on this
    # public, user-owned repo (org-only GitHub feature). Warn loudly, never fail.
    warn_path_lock_descoped()
    print(
        "\nSelf-anchor check passed: both hard requirements hold (both floor "
        "checks required + branch protection readable). The floor.yml path lock "
        "is descoped (see the warning above)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
