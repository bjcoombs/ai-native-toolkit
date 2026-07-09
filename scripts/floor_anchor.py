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

# The required status check context (the floor.yml job name) and the path the
# push ruleset must restrict.
FLOOR_CONTEXT = os.environ.get("FLOOR_CONTEXT", "floor enforcement")
FLOOR_PATH = ".github/workflows/floor.yml"


class AnchorError(Exception):
    """A settings gap or a failure to confirm the anchor. Always fails closed."""


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
    """Fail unless FLOOR_CONTEXT is a required status check on ``branch``.

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
    contexts.update(_ruleset_required_contexts(repo, branch, token))

    if FLOOR_CONTEXT not in contexts:
        raise AnchorError(
            f"floor status check {FLOOR_CONTEXT!r} is NOT required on {branch!r}. "
            f"Required contexts seen: {sorted(contexts) or 'none'}. The floor is "
            "disarmed until it is restored (maintainer out-of-band sign-off)."
        )
    print(f"ok   floor status check {FLOOR_CONTEXT!r} is required on {branch!r}.")


def _ruleset_required_contexts(repo: str, branch: str, token: str) -> set[str]:
    contexts: set[str] = set()
    status, rulesets = _get(f"/repos/{repo}/rulesets", token)
    if status in (401, 403):
        raise AnchorError(
            f"cannot read rulesets (HTTP {status}). Set FLOOR_ANCHOR_TOKEN to a "
            "PAT with 'Administration: read'."
        )
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


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("FAIL GITHUB_REPOSITORY is not set.", file=sys.stderr)
        return 1
    token = os.environ.get("FLOOR_ANCHOR_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "FAIL no token available. Set FLOOR_ANCHOR_TOKEN (fine-grained PAT, "
            "'Administration: read') so the anchor can read repo settings.",
            file=sys.stderr,
        )
        return 1
    try:
        branch = _default_branch(repo, token)
        check_required_check(repo, branch, token)
        check_path_restriction(repo, token)
    except AnchorError as exc:
        print(f"FAIL self-anchor: {exc}", file=sys.stderr)
        return 1
    print("\nSelf-anchor check passed: the floor is still enforced by repo settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
