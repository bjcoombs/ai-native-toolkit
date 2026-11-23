#!/usr/bin/env bash
# The AI-readiness regression gate as a GitLab CI job script (#1152).
#
# templates/assess-gate.yml (at the repository root, usable with `include:`
# and as a CI/CD component) clones this toolkit at a pinned tag and runs this
# script. It is the GitLab twin of the composite GitHub Action (action.yml) and
# keeps the same contract:
#
# - The same headless gate: the same three render commands, then
#   assess_gate.py. No gate logic lives here.
# - Pinned supply chain: ripgrep 14.1.1 and uv 0.5.9, the Action's pins.
# - Infrastructure failures (a tool install, uv, a render crash) print a skip
#   notice and exit 0. Only assess_gate.py's exit code can fail the job.
# - The summary goes to the job log, and as a merge request note when a token
#   allows it. A missing or refused token is a notice, never a failure.
#
# Environment (the template sets these from its inputs):
#   ASSESS_PROJECT_DIR        the checked-out project (default: $CI_PROJECT_DIR)
#   ASSESS_CONFIG             gate config, relative to the project (default: .assess/config.toml)
#   ASSESS_FAIL_ON_GATE_LOSS  "true" passes --fail-on-gate-loss to assess_gate.py
#   ASSESS_MR_NOTE            "true" posts the summary as a merge request note, one new
#                             note per pipeline
#   ASSESS_GATE_TOKEN         optional token with api scope for the note (PRIVATE-TOKEN);
#                             without it the note is tried with CI_JOB_TOKEN
#   CI_API_V4_URL, CI_PROJECT_ID, CI_MERGE_REQUEST_IID, CI_JOB_TOKEN,
#   CI_MERGE_REQUEST_TARGET_BRANCH_NAME: set by GitLab. With the gate-loss check on,
#   base_baseline.sh swaps in the target branch's .assess/prior-level.json first.
#
# Tests: skills/assess/tests/test_gitlab_gate.py runs this script against a
# fixture project with stubbed curl.
set -u

RG_VERSION=14.1.1
UV_VERSION=0.5.9

TOOLKIT_ASSESS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${ASSESS_PROJECT_DIR:-${CI_PROJECT_DIR:-$PWD}}"
CONFIG="${ASSESS_CONFIG:-.assess/config.toml}"

notice() { echo "assess-gate: $*"; }

ensure_rg() {
    command -v rg >/dev/null 2>&1 && return 0
    local deb="ripgrep_${RG_VERSION}-1_amd64.deb"
    if command -v curl >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 \
        && [ "$(dpkg --print-architecture 2>/dev/null)" = "amd64" ] \
        && curl -fsSLo "/tmp/$deb" "https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/$deb" \
        && dpkg -i "/tmp/$deb" >/dev/null 2>&1; then
        return 0
    fi
    notice "ripgrep install failed - the marker scan degrades to reduced coverage (warn-only contract)."
}

ensure_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    if python3 -m pip install --quiet "uv==${UV_VERSION}" >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
        return 0
    fi
    if command -v curl >/dev/null 2>&1 && curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh >/dev/null 2>&1; then
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        command -v uv >/dev/null 2>&1 && return 0
    fi
    return 1
}

render() {
    mkdir -p "$PROJECT/.assess" || return 1
    (
        cd "$TOOLKIT_ASSESS" || exit 1
        uv run scripts/complexity-treemap.py "$PROJECT" \
            -o "$PROJECT/.assess/complexity-heatmap.svg" \
            --stats "$PROJECT/.assess/complexity-stats.json" \
            && uv run scripts/assess_core.py "$PROJECT" \
            && uv run scripts/assess_report.py "$PROJECT"
    )
}

post_note() {
    # $1: the file holding the gate output.
    [ "${ASSESS_MR_NOTE:-true}" = "true" ] || return 0
    if [ -z "${CI_MERGE_REQUEST_IID:-}" ]; then
        notice "not a merge request pipeline, so no merge request note was posted."
        return 0
    fi
    # The merge request's own project: a fork's pipeline runs in the source project.
    local project_id="${CI_MERGE_REQUEST_PROJECT_ID:-${CI_PROJECT_ID:-}}"
    if [ -z "${CI_API_V4_URL:-}" ] || [ -z "$project_id" ]; then
        notice "the GitLab API address is not set, so no merge request note was posted."
        return 0
    fi
    local header
    if [ -n "${ASSESS_GATE_TOKEN:-}" ]; then
        header="PRIVATE-TOKEN: ${ASSESS_GATE_TOKEN}"
    elif [ -n "${CI_JOB_TOKEN:-}" ]; then
        header="JOB-TOKEN: ${CI_JOB_TOKEN}"
    else
        notice "no token is available, so the summary stays in this job log. Set ASSESS_GATE_TOKEN to post it on the merge request."
        return 0
    fi
    local body status
    body="$(printf '#### AI-readiness gate\n\n```\n%s\n```\n' "$(cat "$1")")"
    status="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 --max-time 30 --request POST \
        --header "$header" --form-string "body=$body" \
        "${CI_API_V4_URL}/projects/${project_id}/merge_requests/${CI_MERGE_REQUEST_IID}/notes" 2>/dev/null)"
    case "$status" in
        2??) notice "posted the summary as a merge request note." ;;
        *) notice "the merge request note was not posted (HTTP ${status:-none}), so the summary stays in this job log. A token with api scope in ASSESS_GATE_TOKEN can post it." ;;
    esac
    return 0
}

main() {
    ensure_rg
    if ! ensure_uv; then
        notice "uv unavailable - skipping the assessment this run (warn-only contract; it runs again on the next push)."
        return 0
    fi
    if [ "${ASSESS_FAIL_ON_GATE_LOSS:-true}" = "true" ]; then
        # Measure the change against its target branch's baseline, never its own.
        bash "$TOOLKIT_ASSESS/scripts/base_baseline.sh" "$PROJECT" "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-}"
    fi
    if ! render; then
        notice "skipped: infrastructure failure rendering the snapshot - not a finding. The gate runs again on the next push."
        return 0
    fi
    local args=("$PROJECT" --config "$PROJECT/$CONFIG" --summary)
    [ "${ASSESS_FAIL_ON_GATE_LOSS:-true}" = "true" ] && args+=(--fail-on-gate-loss)
    local out rc
    out="$(mktemp)"
    # stdout is the verdict and the note body; stderr (uv progress, notices) stays in the log.
    (cd "$TOOLKIT_ASSESS" && uv run scripts/assess_gate.py "${args[@]}") >"$out"
    rc=$?
    cat "$out"
    post_note "$out"
    rm -f "$out"
    return "$rc"
}

main
exit $?
