#!/usr/bin/env python3
"""Self-anchor check for the floor (PRD E2): is the floor still enforced?

The floor markers and clause checks only bite while ``floor.yml`` is a *required*
status check. A settings change that drops it silently disarms the whole floor.
This script closes that gap: it queries the live GitHub API on every run and
HARD-FAILS (fail-closed) unless BOTH of these hold:

  1. both floor status checks (``floor enforcement`` and ``floor self-anchor``)
     are still required on the default branch,
  2. branch protection is readable at all -- i.e. an anchor token with
     admin:read is present, so requirement 1 can actually be confirmed, and
  3. clause iii's sign-off artefact is intact: the ``floor-signoff`` deployment
     environment exists with the repository owner as a required reviewer, and
     the checked-out ``floor.yml`` still wires that environment into a job the
     ``floor enforcement`` job needs. Either half alone is decorative -- an
     environment nothing references never asks for a review, and a job pointing
     at an environment with no required reviewer approves itself.

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
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"

# The required status check context (the deterministic floor.yml job name) and
# the path the push ruleset must restrict.
FLOOR_CONTEXT = os.environ.get("FLOOR_CONTEXT", "floor enforcement")
# The anchor job's own context. It must ALSO be a required check: if only the
# deterministic layer is required, a later PR could drop it from protection, the
# anchor would go red without gating, and the floor would silently disarm (E2).
ANCHOR_CONTEXT = os.environ.get("ANCHOR_CONTEXT", "floor self-anchor")
FLOOR_PATH = ".github/workflows/floor.yml"
# The deployment environment that carries clause iii's sign-off artefact. Read
# from the environment like the two contexts above, so the fail-closed branches
# below can be driven against a name that deliberately does not exist.
SIGNOFF_ENVIRONMENT = os.environ.get("FLOOR_SIGNOFF_ENVIRONMENT", "floor-signoff")

# Where the workflow definitions live, relative to the repository root, and the
# line shapes a job identity takes in them. Jobs sit at two-space indentation
# under ``jobs:``; their ``name:`` sits one level deeper, while steps sit
# deeper still and behind a ``- ``, so a four-space ``name:`` is unambiguously a
# job name. A job with no ``name:`` is still a real check context -- GitHub
# falls back to the job *id* -- so the two-space id key is collected too. This
# is a regex over lines rather than a YAML parse on purpose: the self-anchor job
# runs bare ``python`` with no dependency install, so PyYAML is not available to
# it.
WORKFLOW_DIR = ".github/workflows"
WORKFLOW_GLOBS = ("*.yml", "*.yaml")
JOB_NAME_RE = re.compile(r"^\s{4}name: (.+)$")
JOB_ID_RE = re.compile(r"^ {2}([A-Za-z0-9_-]+):\s*$")
JOBS_KEY_RE = re.compile(r"^jobs:\s*$")
TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+:")
# Job-level keys the wiring check reads, at the same four-space depth as
# ``name:``. ``needs:`` takes three shapes (a bare id, an inline list, or a
# block list), so its value is captured raw and split below.
JOB_ENVIRONMENT_RE = re.compile(r"^\s{4}environment:\s*(.+?)\s*$")
JOB_NEEDS_RE = re.compile(r"^\s{4}needs:\s*(.*?)\s*$")
BLOCK_LIST_ITEM_RE = re.compile(r"^\s{6}-\s*(.+?)\s*$")
REPO_ROOT = Path(__file__).resolve().parent.parent

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
    """The three maintainer-only, out-of-band steps that arm the anchor.

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
     gh secret set FLOOR_ANCHOR_TOKEN --repo "{repo}" --app dependabot   # the same PAT again: Dependabot runs read a separate secret store

     Both are needed. Actions secrets and Dependabot secrets are separate
     stores, so a rotation that sets only the first leaves every
     Dependabot-triggered run without a token and red on this check.

  3. Create clause iii's sign-off artefact: a deployment environment named
     {SIGNOFF_ENVIRONMENT} whose SOLE required reviewer is the repository
     owner, and no deployment branch policy. It is a settings object, not a
     file, which is the point -- the approval it records lives outside the diff
     under review:

     gh api "repos/{repo}/environments/{SIGNOFF_ENVIRONMENT}" --method PUT \\
       --input - <<'JSON'
     {{"reviewers": [{{"type": "User", "id": <the owner's numeric user id>}}]}}
     JSON

     The owner's numeric id comes from: gh api "repos/{repo}" --jq .owner.id
     An environment with no required reviewer auto-approves its own deployment,
     so the 'floor sign-off' job would go green with no maintainer click.

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


def check_required_check(repo: str, branch: str, token: str) -> set[str]:
    """Fail unless BOTH floor contexts are required status checks on ``branch``.

    The deterministic ``FLOOR_CONTEXT`` job *and* the ``ANCHOR_CONTEXT`` job (this
    self-anchor check) must each be required. Verifying only the deterministic one
    leaves a disarm path: if the anchor job is not itself required, a later PR
    could drop ``FLOOR_CONTEXT`` from protection -- the anchor would go red but no
    longer gate, and the floor would silently disarm (PRD E2). So we fail closed
    unless every floor context is present, naming exactly which one is missing.

    Checks both classic branch protection and repo rulesets, since either can
    supply a required check. A permission error (admin:read missing) fails closed.
    Returns every required context it saw, so the caller can check them against
    the workflows on this branch.
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
    return contexts


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


def _workflow_files(base: Path) -> list[Path]:
    """Every workflow definition under ``base``, both ``.yml`` and ``.yaml``.

    GitHub reads either extension, so globbing only one leaves whole workflows
    -- and every context they produce -- invisible to the subset check below.
    """
    directory = base / WORKFLOW_DIR
    found: set[Path] = set()
    for pattern in WORKFLOW_GLOBS:
        found.update(directory.glob(pattern))
    return sorted(found)


def _workflow_job_names(root: Path | None = None) -> set[str]:
    """Every check-run name a job in ``.github/workflows`` can produce.

    That is both the job ``name:`` literals and the job *ids*: GitHub names a
    check run after the job's ``name:`` when it has one and after the job id
    when it does not, so a collector that reads only ``name:`` lines misses
    every unnamed job (this repo has two) and would fail the caller closed on a
    context that is in fact produced fine.

    Collecting ids as well is strictly more permissive and cannot mask a
    rename: every required context on this repo contains a space and no YAML
    job id can, so an id never stands in for a renamed ``name:`` literal.

    Read from the checked-out branch, so a rename is seen in the PR that makes
    it rather than after merge. An unreadable or absent workflow directory
    yields an empty set, which the caller turns into a fail-closed error.
    """
    base = REPO_ROOT if root is None else Path(root)
    names: set[str] = set()
    for workflow in _workflow_files(base):
        try:
            text = workflow.read_text(encoding="utf-8")
        except OSError:
            continue
        in_jobs = False
        for line in text.splitlines():
            if JOBS_KEY_RE.match(line):
                in_jobs = True
                continue
            # A new top-level key ends the jobs block; comments and blank lines
            # inside it do not.
            if in_jobs and TOP_LEVEL_KEY_RE.match(line):
                in_jobs = False
            match = JOB_NAME_RE.match(line)
            if match:
                names.add(match.group(1).strip().strip("\"'"))
                continue
            if in_jobs:
                job_id = JOB_ID_RE.match(line)
                if job_id:
                    names.add(job_id.group(1))
    return names


def check_contexts_have_workflows(contexts: set[str], root: Path | None = None) -> None:
    """Fail unless every required context is produced by a job on this branch.

    A required status check that no job emits is never reported, and GitHub
    treats a context that never arrives as pending rather than failing -- so a
    job rename orphans the context and the merge blocks on a check that can
    never turn green, or (where the context is dropped instead) a floor layer
    disarms with nothing red. Comparing the required set against the job
    identities on the checked-out branch -- ``name:`` literals plus the job ids
    GitHub falls back to for unnamed jobs -- moves that discovery into the PR
    that renames the job.
    """
    job_names = _workflow_job_names(root)
    orphaned = sorted(ctx for ctx in contexts if ctx not in job_names)
    if orphaned:
        named = ", ".join(repr(ctx) for ctx in orphaned)
        raise AnchorError(
            f"required status check context(s) {named} are produced by no job "
            f"name: or job id in {WORKFLOW_DIR}/{{{','.join(WORKFLOW_GLOBS)}}} on "
            "this branch. A required context no job emits never arrives, so the "
            "check it gates can never turn green. Job names seen: "
            f"{sorted(job_names) or 'none'}. Rename the job back, or update "
            "branch protection in the same change."
        )
    print(
        f"ok   all {len(contexts)} required context(s) are produced by a job "
        f"name: or job id in {WORKFLOW_DIR}/{{{','.join(WORKFLOW_GLOBS)}}}."
    )


def _repo_owner(repo: str, token: str) -> str:
    """The repository owner's login, or fail closed.

    The owner is the identity clause iii names as the signer, so an inability
    to resolve it is an inability to confirm the sign-off artefact.
    """
    status, data = _get(f"/repos/{repo}", token)
    if status != 200 or not isinstance(data, dict):
        raise AnchorError(f"cannot read repo metadata (HTTP {status}): {data}")
    owner = (data.get("owner") or {}).get("login")
    if not owner:
        raise AnchorError(f"repo metadata for {repo!r} names no owner login: {data}")
    return str(owner)


def _required_reviewer_logins(data: dict) -> set[str]:
    """Every USER login listed by a ``required_reviewers`` protection rule.

    Team reviewers are deliberately ignored: clause iii names the maintainer,
    and a team the maintainer may later leave is not the same guarantee.
    """
    logins: set[str] = set()
    for rule in data.get("protection_rules", []) or []:
        if not isinstance(rule, dict) or rule.get("type") != "required_reviewers":
            continue
        for entry in rule.get("reviewers", []) or []:
            if not isinstance(entry, dict) or entry.get("type") != "User":
                continue
            login = (entry.get("reviewer") or {}).get("login")
            if login:
                logins.add(str(login))
    return logins


def check_signoff_environment(repo: str, token: str) -> None:
    """Fail unless clause iii's sign-off environment exists and gates on the owner.

    The environment is the artefact clause iii names: a deployment review with
    an actor and a timestamp GitHub records OUTSIDE the diff, so the pull
    request under review cannot forge it. Deleting the environment, or dropping
    the owner from its required reviewers, would turn the ``floor sign-off``
    job into a no-op that auto-approves every floor change -- green, silent, and
    exactly the disarm this script exists to catch. So every branch here fails
    CLOSED: a 404, any other non-200, and an unreadable or owner-less reviewer
    list are all failures, never passes.
    """
    env = SIGNOFF_ENVIRONMENT
    status, data = _get(f"/repos/{repo}/environments/{env}", token)
    if status == 404:
        raise AnchorError(
            f"the {env!r} deployment environment does not exist on {repo}. "
            "FLOOR.md clause iii records the maintainer's out-of-band approval "
            f"as a deployment review of {env!r}; with no such environment the "
            "'floor sign-off' job requests no review and a floor change "
            "approves itself."
        )
    if status != 200 or not isinstance(data, dict):
        raise AnchorError(
            f"cannot read the {env!r} deployment environment (HTTP {status}): "
            f"{data}. The anchor cannot confirm clause iii's sign-off artefact, "
            "so it fails closed."
        )
    owner = _repo_owner(repo, token)
    reviewers = _required_reviewer_logins(data)
    if owner not in reviewers:
        raise AnchorError(
            f"the {env!r} environment does not require a review from the "
            f"repository owner {owner!r}. Required user reviewers seen: "
            f"{sorted(reviewers) or 'none'}. An environment with no required "
            "reviewer approves its own deployment, so the 'floor sign-off' job "
            "would go green with no maintainer click (FLOOR.md clause iii)."
        )
    print(
        f"ok   the {env!r} environment exists and requires a review from the "
        f"repository owner ({owner!r})."
    )


def _floor_workflow_jobs(text: str) -> dict[str, list[str]]:
    """Split ``floor.yml`` into ``{job id: its lines}``.

    A line scan rather than a YAML parse, for the same reason
    ``_workflow_job_names`` is one: the self-anchor job runs bare ``python``
    with no dependency install, so PyYAML is not available to it.
    """
    jobs: dict[str, list[str]] = {}
    in_jobs = False
    current: str | None = None
    for line in text.splitlines():
        if JOBS_KEY_RE.match(line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if TOP_LEVEL_KEY_RE.match(line):
            break  # a new top-level key ends the jobs block
        job_id = JOB_ID_RE.match(line)
        if job_id:
            current = job_id.group(1)
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return jobs


def _job_needs(lines: list[str]) -> set[str]:
    """The job ids a job's ``needs:`` names, across all three YAML shapes."""
    needs: set[str] = set()
    for index, line in enumerate(lines):
        match = JOB_NEEDS_RE.match(line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            needs.update(
                part.strip().strip("\"'[]")
                for part in inline.strip("[]").split(",")
                if part.strip().strip("\"'[]")
            )
            continue
        for follow in lines[index + 1:]:  # a block list under `needs:`
            item = BLOCK_LIST_ITEM_RE.match(follow)
            if not item:
                break
            needs.add(item.group(1).strip().strip("\"'"))
    return needs


def check_workflow_wiring(root: Path | None = None) -> None:
    """Fail unless the checked-out ``floor.yml`` still wires the environment in.

    An existing environment proves nothing on its own: if no job carries
    ``environment: <name>``, no deployment review is ever requested, and if the
    job that carries it is not in ``floor enforcement``'s ``needs``, a refusal
    cannot reach the required context. Reading the CHECKED-OUT file (not the
    default branch) moves that discovery into the pull request that unwires it,
    which is the same reason ``check_contexts_have_workflows`` reads this branch.
    """
    env = SIGNOFF_ENVIRONMENT
    base = REPO_ROOT if root is None else Path(root)
    path = base / FLOOR_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AnchorError(
            f"cannot read {FLOOR_PATH} to confirm the {env!r} environment is "
            f"still wired into the floor jobs ({exc}). Fails closed."
        ) from exc

    jobs = _floor_workflow_jobs(text)
    quoted = {env, f"'{env}'", f'"{env}"'}
    env_jobs = {
        job_id
        for job_id, lines in jobs.items()
        for line in lines
        if (m := JOB_ENVIRONMENT_RE.match(line)) and m.group(1) in quoted
    }
    if not env_jobs:
        raise AnchorError(
            f"no job in {FLOOR_PATH} carries `environment: {env}`. Without it "
            "GitHub requests no deployment review, so a floor change merges "
            "with no maintainer approval (FLOOR.md clause iii)."
        )

    enforcement = [
        job_id
        for job_id, lines in jobs.items()
        for line in lines
        if (m := JOB_NAME_RE.match(line))
        and m.group(1).strip().strip("\"'") == FLOOR_CONTEXT
    ]
    if not enforcement:
        raise AnchorError(
            f"no job in {FLOOR_PATH} is named {FLOOR_CONTEXT!r}, so the "
            f"{env!r} sign-off cannot be wired into the required context. "
            "Fails closed."
        )
    needs = set()
    for job_id in enforcement:
        needs |= _job_needs(jobs[job_id])
    wired = sorted(env_jobs & needs)
    if not wired:
        raise AnchorError(
            f"the {FLOOR_CONTEXT!r} job does not declare any {env!r} job in "
            f"`needs:` (job(s) carrying the environment: {sorted(env_jobs)}; "
            f"needs seen: {sorted(needs) or 'none'}). A sign-off the required "
            "context does not depend on cannot turn a refusal red, and branch "
            "protection reads an absent context as satisfied."
        )
    print(
        f"ok   {FLOOR_PATH} wires the {env!r} environment into job "
        f"{wired[0]!r}, which the {FLOOR_CONTEXT!r} job needs."
    )


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
        contexts = check_required_check(repo, branch, token)
        check_contexts_have_workflows(contexts)
        check_signoff_environment(repo, token)
        check_workflow_wiring()
    except AnchorError as exc:
        return _fail(str(exc), repo)
    # E2 descope: the floor.yml path lock is a documented capability gap on this
    # public, user-owned repo (org-only GitHub feature). Warn loudly, never fail.
    warn_path_lock_descoped()
    print(
        "\nSelf-anchor check passed: all three hard requirements hold (both "
        "floor checks required + branch protection readable + clause iii's "
        f"{SIGNOFF_ENVIRONMENT!r} sign-off environment present and wired). The "
        "floor.yml path lock is descoped (see the warning above)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
