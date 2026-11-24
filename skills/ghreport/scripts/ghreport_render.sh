# shellcheck shell=bash
# ghreport_render.sh - the assembler: tables, attention lists and the report.
# Sourced by ghreport.sh, never run on its own: it defines functions and
# reads the globals ghreport.sh sets (ORG, PROVIDER, GITLAB_HOST, ...).

# ---- assembler --------------------------------------------------------------
# Shared jq helpers, so the headline/table/attention views agree on what
# "has an open alert" means and how a no-access cell renders.
JQ_DEFS='
  def has_open_alerts:
    ((.dependabot|type)=="number" and .dependabot>0)
    or ((.code_scanning|type)=="number" and .code_scanning>0)
    or ((.secret_scanning|type)=="number" and .secret_scanning>0);
  def needs_attention:
    .ci=="fail" or has_open_alerts or .protection=="unprotected" or (.unreachable==true);
  def cell(x): if x=="no-access" then "n/a" else (x|tostring) end;
'

# Emit the markdown table (header + one row per repo) for a JSON array on stdin.
render_table() {
    echo "| Repo | PRs | CI | Dependabot | CodeQL | Secrets | Protection |"
    echo "|------|-----|----|-----------|--------|---------|------------|"
    jq -r "$JQ_DEFS"'
      sort_by(.repo)[] |
      "| \(.repo) | "
      + (if .unreachable then "- | - | - | - | - | could not assess |"
         else
           "\(.open_prs)\(if .draft_prs>0 then " (\(.draft_prs) draft)" else "" end) | "
           + "\(.ci)\(if .ci=="fail" and (.failing_workflows|length)>0 then " ⚠" else "" end) | "
           + "\(cell(.dependabot)) | \(cell(.code_scanning)) | \(cell(.secret_scanning)) | "
           + "\(.protection) |"
         end)'
}

# Emit the "Needs attention" bullet list for a JSON array on stdin.
render_attention() {
    jq -r "$JQ_DEFS"'
      sort_by(.repo)[] | select(needs_attention) |
      "- **\(.repo)**: " + ([
        (if .unreachable then "could not assess (repo metadata unreadable)" else empty end),
        (if .ci=="fail" then "CI failing" + (if (.failing_workflows|length)>0 then " (\(.failing_workflows))" else "" end) else empty end),
        (if ((.dependabot|type)=="number" and .dependabot>0) then "\(.dependabot) Dependabot alert(s)" else empty end),
        (if ((.code_scanning|type)=="number" and .code_scanning>0) then "\(.code_scanning) code-scanning alert(s)" else empty end),
        (if ((.secret_scanning|type)=="number" and .secret_scanning>0) then "\(.secret_scanning) secret-scanning alert(s)" else empty end),
        (if .protection=="unprotected" then "default branch unprotected" else empty end)
      ] | join("; "))'
}

# GitLab views. Unreadable cells render as the literal "no-access" /
# "unknown" so the report says exactly what the API said.
JQ_DEFS_GITLAB='
  def has_open_vulns: (.vulnerabilities|type)=="number" and .vulnerabilities>0;
  def needs_attention:
    .pipeline=="failed" or has_open_vulns or .protection=="unprotected" or (.unreachable==true);
'

render_table_gitlab() {
    echo "| Project | Branch | MRs | Pipeline | Vulnerabilities | Protection |"
    echo "|---------|--------|-----|----------|-----------------|------------|"
    jq -r "$JQ_DEFS_GITLAB"'
      sort_by(.repo)[] |
      "| \(.repo) | "
      + (if .unreachable then "- | - | - | - | could not assess |"
         else
           "\(if .empty_repo==true then "empty" else .default_branch end) | "
           + "\(.open_mrs)\(if (.draft_mrs // 0)>0 then " (\(.draft_mrs) draft)" else "" end) | "
           + "\(.pipeline) | \(.vulnerabilities) | \(.protection) |"
         end)'
}

render_attention_gitlab() {
    jq -r "$JQ_DEFS_GITLAB"'
      sort_by(.repo)[] | select(needs_attention) |
      "- **\(.repo)**: " + ([
        (if .unreachable then "could not assess (project metadata unreadable)" else empty end),
        (if .pipeline=="failed" then "pipeline failed on \(.default_branch)" else empty end),
        (if has_open_vulns then "\(.vulnerabilities) open vulnerabilit\(if .vulnerabilities==1 then "y" else "ies" end)" else empty end),
        (if .protection=="unprotected" then "default branch unprotected" else empty end)
      ] | join("; "))'
}

# One line of headline counts for a JSON array on stdin. Unreadable MR and
# vulnerability counts are named, so a total of 0 is never read as clean.
headline_gitlab() {
    jq -r "$JQ_DEFS_GITLAB"'
      ([.[]|select(.unreachable!=true)]) as $ok
      | "\(length) project(s): \([.[]|select(.pipeline=="failed")]|length) with a failed pipeline, "
        + "\([.[]|select(has_open_vulns)]|length) with open vulnerabilities, "
        + "\([.[]|select(.protection=="unprotected")]|length) with an unprotected default branch, "
        + "\([.[]|.open_mrs|numbers]|add // 0) open MR(s) total"
        + (([$ok[]|select(.open_mrs=="no-access")]|length) as $n | if $n>0 then ", MRs unreadable on \($n)" else "" end)
        + (([$ok[]|select(.vulnerabilities=="no-access")]|length) as $n | if $n>0 then ", vulnerabilities no-access on \($n)" else "" end)
        + (([.[]|select(.unreachable==true)]|length) as $n | if $n>0 then ", \($n) could not be assessed" else "" end)
        + (([$ok[]|select(.empty_repo==true)]|length) as $n | if $n>0 then ", \($n) empty" else "" end)'
}

legend_gitlab() {
    echo "- **Pipeline**: status of the latest finished pipeline (\`success\` or \`failed\`) among the last 20 on the default branch; running, pending, canceled, skipped and manual pipelines are passed over. \`none\` means no finished pipeline was found, \`unknown\` that the pipelines API could not be read."
    echo "- **no-access** in a count column: the API refused or failed. For vulnerabilities that is the tier, the role, or the endpoint being unavailable on this GitLab version (GitLab documents the REST vulnerabilities endpoint as deprecated). It is not the same as a confirmed zero."
    echo "- **Protection**: \`unknown\` means the protected-branches API could not be read; \`unprotected\` means it listed no rule covering the default branch."
    echo "- \`empty\` in Branch, Pipeline and Protection: the repository has no commits, so GitLab reports no default branch. Pipeline and protection are not checked, so emptiness alone does not put a project under Needs attention; its open vulnerabilities, which GitLab keeps from earlier scans, still do."
}

assemble() {
    local dir=$1
    shopt -s nullglob
    local frags=("$dir"/*.json)
    local data
    if [ ${#frags[@]} -eq 0 ]; then
        data='[]'
    else
        data=$(jq -s '.' "${frags[@]}")
    fi
    # Settings live in their own subdirectory, so the glob above never reads
    # them as repository rows.
    local sfrags=("$dir"/settings/*.json)
    SETTINGS_DATA=""
    [ ${#sfrags[@]} -gt 0 ] && SETTINGS_DATA=$(jq -s '.' "${sfrags[@]}")

    if [ "$PROVIDER" = gitlab ]; then
        emit_report "$data" "$(echo "$data" | headline_gitlab)" \
            render_table_gitlab render_attention_gitlab legend_gitlab "Projects" "projects"
        return
    fi

    # Headline counts.
    local total failing_ci with_alerts unprotected unreachable open_prs_total
    read -r total failing_ci with_alerts unprotected unreachable open_prs_total < <(
        echo "$data" | jq -r "$JQ_DEFS"'
          [ (length),
            ([.[]|select(.ci=="fail")]|length),
            ([.[]|select(has_open_alerts)]|length),
            ([.[]|select(.protection=="unprotected")]|length),
            ([.[]|select(.unreachable==true)]|length),
            ([.[]|.open_prs // 0]|add // 0)
          ] | @tsv')

    local headline="$total repo(s): $failing_ci with failing CI, $with_alerts with open security alerts, $unprotected with an unprotected default branch, $open_prs_total open PR(s) total"
    [ "$unreachable" -gt 0 ] && headline="$headline, $unreachable could not be assessed"

    emit_report "$data" "$headline" render_table render_attention legend_github "Repos" "repos"
}

legend_github() {
    echo "- **CI**: latest completed run per workflow on the default branch (\`pass\` / \`fail\` / \`none\`)."
    echo "- **n/a** in an alert column: the alert API returned no access - the feature is disabled on the repo or admin access is required. Either way it is not the same as a confirmed zero."
    echo "- **Protection**: \`unknown\` means the protection API denied access; \`unprotected\` means it confirmed no protection."
}

# Terminal summary plus the markdown file, shared by both providers.
# $1 data, $2 headline, $3 table fn, $4 attention fn, $5 legend fn,
# $6 table heading, $7 noun for the clean message.
emit_report() {
    local data=$1 headline=$2 table_fn=$3 attention_fn=$4 legend_fn=$5 heading=$6 noun=$7
    local today
    today=$(date +%Y-%m-%d)

    # ---- terminal ----
    echo ""
    echo "====== Org State: $ORG ($today) ======"
    echo "$headline"
    echo ""
    echo "$data" | "$table_fn"
    echo ""
    local attention
    attention=$(echo "$data" | "$attention_fn")
    if [ -n "$attention" ]; then
        echo "Needs attention:"
        echo "$attention"
    else
        echo "Needs attention: none - all assessed $noun are clean."
    fi
    local settings_title="Org settings"
    [ "$PROVIDER" = gitlab ] && settings_title="Group settings"
    if [ -n "$SETTINGS_DATA" ]; then
        echo ""
        echo "$settings_title:"
        echo "$SETTINGS_DATA" | settings_headline
        echo "$SETTINGS_DATA" | render_settings
    fi

    # ---- file ----
    if [ "$NO_FILE" = false ]; then
        # A GitLab label can hold a group path; keep the file name flat.
        local report_file="$ROOT/org-state-${ORG//\//-}-$today.md"
        {
            echo "# Org state: $ORG"
            echo ""
            echo "_Generated $today by ghreport (read-only)._"
            echo ""
            echo "$headline"
            echo ""
            echo "## $heading"
            echo ""
            echo "$data" | "$table_fn"
            echo ""
            echo "## Needs attention"
            echo ""
            if [ -n "$attention" ]; then
                echo "$attention"
            else
                echo "None - all assessed $noun are clean."
            fi
            echo ""
            if [ -n "$SETTINGS_DATA" ]; then
                echo "## $settings_title"
                echo ""
                echo "$SETTINGS_DATA" | settings_headline
                echo ""
                echo "$SETTINGS_DATA" | render_settings
                echo ""
                settings_legend
                echo ""
            fi
            echo "## Legend"
            echo ""
            "$legend_fn"
        } > "$report_file"
        echo ""
        echo "Report written to: $report_file"
    fi
}
