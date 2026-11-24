# shellcheck shell=bash
# ghreport_settings.sh - org / group settings: the queries and their rendering.
# Sourced by ghreport.sh, never run on its own: it defines functions and
# reads the globals ghreport.sh sets (ORG, PROVIDER, GITLAB_HOST, ...).

# ---- org / group settings ---------------------------------------------------
# One JSON fragment per org (GitHub) or group (GitLab):
#   {scope, provider, two_factor, push_rules, default_branch_protection, rulesets}
# Each setting is {value, reason}: value is the setting, "off", or "unknown";
# reason is null unless value is "unknown". An optional note qualifies a
# readable value (push_rules "off" names its non-active rulesets in it).
# A failed or unreadable call is always "unknown" with the reason, never "off".

# The reason for an unreadable response, from a call's captured output.
http_reason() {
    local code
    code=$(grep -o 'HTTP [0-9][0-9][0-9]' <<< "$1" | tail -1 || true)
    case "$code" in
        "HTTP 401"|"HTTP 403") echo "denied: no access or tier ($code)" ;;
        "HTTP 404") echo "not found: no access, tier, or endpoint missing ($code)" ;;
        "") echo "unreadable response" ;;
        *) echo "request failed ($code)" ;;
    esac
}

JQ_SETTING_DEFS='
  def unk($r): {value:"unknown", reason:$r};
  def val($v): {value:$v, reason:null};
'

# A fragment with every setting unknown for one reason.
settings_all_unknown() {
    jq -n --arg scope "$1" --arg provider "$PROVIDER" --arg r "$2" '
      {value:"unknown", reason:$r} as $u
      | {scope:$scope, provider:$provider, two_factor:$u, push_rules:$u,
         default_branch_protection:$u, rulesets:$u}' > "$3"
}

query_org_settings_github() {
    local out=$1 raw org_json='{}' org_err="" rs_json='[]' rs_err="" details='[]' detail_err=""
    if raw=$(gh api "orgs/$ORG" 2>&1) && json_object_with login "$raw"; then
        org_json=$raw
    else
        org_err=$(http_reason "$raw")
        # A personal account answers orgs/ with a 404 because it has no org
        # settings at all; say so rather than suggest missing access.
        if [ "$(gh api "users/$ORG" --jq '.type' 2>/dev/null || true)" = "User" ]; then
            settings_all_unknown "$ORG" "personal account: no org settings" "$out"
            return
        fi
    fi

    # Rulesets carry push rules (an active ruleset with target "push"; when
    # every push ruleset is non-active, off with them named in a note) and
    # default-branch protection for new repositories (an active branch ruleset on ~DEFAULT_BRANCH or ~ALL
    # of every repository). The list omits conditions, so each active branch
    # ruleset is read in full; one unreadable detail makes the verdict unknown.
    if raw=$(gh api "orgs/$ORG/rulesets?per_page=100" --paginate 2>&1) \
        && raw=$(jq -s 'if length>0 and all(.[]; type=="array") then add else error("not a list") end' <<< "$raw" 2>/dev/null); then
        rs_json=$raw
        local id d
        for id in $(jq -r '.[] | select(.target=="branch" and .enforcement=="active") | .id' <<< "$rs_json"); do
            if d=$(gh api "orgs/$ORG/rulesets/$id" 2>&1) && json_object_with id "$d"; then
                details=$(jq -c --argjson d "$d" '. + [$d]' <<< "$details")
            else
                detail_err="ruleset $id unreadable: $(http_reason "$d")"
                break
            fi
        done
    else
        rs_err=$(http_reason "$raw")
    fi

    jq -n "$JQ_SETTING_DEFS"'
      def names: map("\(.name) (\(.enforcement))") | join(", ");
      {scope:$scope, provider:"github",
       two_factor:(
         if $org_err!="" then unk($org_err)
         elif ($org.two_factor_requirement_enabled|type)=="boolean" then
           (if $org.two_factor_requirement_enabled then val("on") else val("off") end)
         else unk("no access: readable by org owners only") end),
       push_rules:(
         if $rs_err!="" then unk($rs_err)
         else ([$rs[] | select(.target=="push")]) as $p
           | if ($p|length)==0 then val("off")
             elif ([$p[] | select(.enforcement=="active")]|length)==0 then
               val("off") + {note:("not active: " + ($p|names))}
             else val($p|names) end end),
       default_branch_protection:(
         if $rs_err!="" then unk($rs_err)
         elif $detail_err!="" then unk($detail_err)
         else ([$details[] | select(
                 ((.conditions.ref_name.include // []) | any(.=="~DEFAULT_BRANCH" or .=="~ALL"))
                 and ((.conditions.repository_name.include // []) | index("~ALL") != null))]) as $c
           | if ($c|length)==0 then val("off") else val("ruleset: " + ($c|map(.name)|join(", "))) end end),
       rulesets:(
         if $rs_err!="" then unk($rs_err)
         elif ($rs|length)==0 then val("off")
         else val("\($rs|length): " + ($rs|names)) end)}' \
        --arg scope "$ORG" --argjson org "$org_json" --arg org_err "$org_err" \
        --argjson rs "$rs_json" --arg rs_err "$rs_err" \
        --argjson details "$details" --arg detail_err "$detail_err" > "$out"
}

# The org's GitHub teams, for the fleet units tree (lib/fleet/fleet_units.py), added
# to the org fragment as `teams: [{slug, parent, repos}]` (parent a team slug
# or null, repos the repository names, both sorted). Not a setting: the report
# does not render it. The key is written only when the team list and every
# team's repository list were read; an unreadable one leaves the key out, so
# an unread team list is never an empty one.
#
# GitHub's "List team repositories" docs say nothing on whether a child
# team's list also holds the repositories it inherits from its parent team
# (only "Check team permissions" says inherited repositories are checked), so
# both cases are handled: a repository a team lists with the same role_name as
# its parent team lists it is taken as inherited and dropped from that team,
# which leaves each repository with the most specific team that holds it
# directly. A child team granted a different role keeps the repository; one
# granted it directly with the parent's role cannot be told apart from
# inheritance and stays on the parent.
#
# Team and repository lists go through files, not jq arguments, so a large
# org stays under the per-argument size limit.
add_org_teams_github() {
    local out=$1 work
    # A personal account has no teams; query_org_settings_github has already
    # marked its fragment, so there is nothing to read and nothing to note.
    if jq -e '.two_factor.reason == "personal account: no org settings"' "$out" >/dev/null 2>&1; then
        return 0
    fi
    work=$(mktemp -d) || return 0
    if ! collect_org_teams_github "$out" "$work"; then
        echo "Note: GitHub teams not read for $ORG; the org fragment has no teams list" >&2
    fi
    rm -rf "$work"
}

collect_org_teams_github() {
    local out=$1 work=$2 raw slug
    raw=$(gh api "orgs/$ORG/teams?per_page=100" --paginate 2>/dev/null) || return 1
    jq -sc 'if length>0 and all(.[]; type=="array") then add else error("not a list") end
            | map(select((.slug | type) == "string" and (.slug | test("^[A-Za-z0-9._-]+$")))
                  | {slug, parent: (.parent.slug? // null)})' \
        <<< "$raw" > "$work/teams.json" 2>/dev/null || return 1
    : > "$work/repos.jsonl"
    for slug in $(jq -r '.[].slug' "$work/teams.json"); do
        raw=$(gh api "orgs/$ORG/teams/$slug/repos?per_page=100" --paginate 2>/dev/null) || return 1
        jq -sc --arg s "$slug" 'if length>0 and all(.[]; type=="array") then add
                                else error("not a list") end
                                | {slug: $s, repos: (map(select(.name|type=="string")
                                    | {key:.name, value:(.role_name // null)}) | from_entries)}' \
            <<< "$raw" >> "$work/repos.jsonl" 2>/dev/null || return 1
    done
    jq --slurpfile teams "$work/teams.json" --slurpfile lists "$work/repos.jsonl" '
      ($lists | map({(.slug): .repos}) | add // {}) as $repos
      | . + {teams: ([$teams[0][] | select($repos[.slug] != null)
         | .parent as $p
         | ($repos[$p // ""] // {}) as $inherited
         | {slug, parent: $p,
            repos: ([$repos[.slug] | to_entries[]
                     | select(.key as $k | ($inherited | has($k) | not) or $inherited[$k] != .value)
                     | .key] | sort)}]
         | sort_by(.slug))}' "$out" > "$work/fragment.json" 2>/dev/null || return 1
    mv "$work/fragment.json" "$out"
}

query_group_settings_gitlab() {
    local group=$1 out=$2 enc raw g_json='{}' g_err="" pr_json='{}' pr_err=""
    enc=$(gitlab_encode "$group")
    if raw=$(gitlab_api "groups/$enc" 2>&1) && json_object_with full_path "$raw"; then
        g_json=$raw
    else
        g_err=$(http_reason "$raw")
    fi
    # A paid-tier endpoint. GitLab answers 404 both for a group with no push
    # rule and for one the token cannot see, so any failure stays unknown.
    if raw=$(gitlab_api "groups/$enc/push_rule" 2>&1) && json_object_with id "$raw"; then
        pr_json=$raw
    else
        pr_err=$(http_reason "$raw")
    fi

    jq -n "$JQ_SETTING_DEFS"'
      def level: {"0":"no one","30":"developers","40":"maintainers","60":"admins"}[tostring] // "level \(.)";
      def levels: map(.access_level | level) | join(", ");
      {scope:$scope, provider:"gitlab",
       two_factor:(
         if $g_err!="" then unk($g_err)
         elif ($g.require_two_factor_authentication|type)=="boolean" then
           (if $g.require_two_factor_authentication then val("on") else val("off") end)
         else unk("field missing from the groups API") end),
       push_rules:(
         if $pr_err!="" then unk($pr_err)
         else ([
             ($pr | to_entries[]
               | select((.key | IN("deny_delete_tag","member_check","prevent_secrets",
                          "commit_committer_check","commit_committer_name_check",
                          "reject_unsigned_commits","reject_non_dco_commits")) and .value==true)
               | .key),
             ($pr | to_entries[]
               | select((.key | endswith("_regex")) and ((.value // "") != ""))
               | .key),
             (if ($pr.max_file_size // 0) > 0 then "max_file_size=\($pr.max_file_size)MB" else empty end)
           ]) as $on
           | if ($on|length)==0 then val("off") else val($on|join(", ")) end end),
       default_branch_protection:(
         if $g_err!="" then unk($g_err)
         elif ($g.default_branch_protection|type)=="number" then
           ({"0":"off","1":"partial (developers can push, no force push)","2":"full",
             "3":"protected against pushes (developers can merge)",
             "4":"full after the initial push"}[$g.default_branch_protection|tostring]) as $v
           | if $v==null then unk("unrecognised level \($g.default_branch_protection)") else val($v) end
         elif ($g.default_branch_protection_defaults|type)=="object"
              and ($g.default_branch_protection_defaults|length)>0 then
           $g.default_branch_protection_defaults as $d
           | ($d.allowed_to_push // [] | any(.[]; (.access_level // 0) > 0 and (.access_level // 0) <= 30)) as $dev_push
           | if $d.allow_force_push==true and $dev_push then val("off")
             else val("push: \($d.allowed_to_push // [] | levels), merge: \($d.allowed_to_merge // [] | levels)"
                      + (if $d.allow_force_push==true then ", force push: allowed" else "" end)) end
         else unk("field missing from the groups API") end),
       rulesets:unk("endpoint missing: GitLab has no group rulesets")}' \
        --arg scope "$group" --argjson g "$g_json" --arg g_err "$g_err" \
        --argjson pr "$pr_json" --arg pr_err "$pr_err" > "$out"
}

# The groups a settings section covers: --group, else each group you belong
# to whose parent you do not, as discovery starts from them. Fails when the
# group list cannot be read, so the caller can report that rather than drop
# the section.
gitlab_settings_groups() {
    if [ -n "$GITLAB_GROUP" ]; then
        echo "$GITLAB_GROUP"
        return
    fi
    local raw
    raw=$(gitlab_api_all "groups?min_access_level=10&per_page=100" 2>/dev/null) || return 1
    jq -e -s 'length>0 and all(.[]; type=="array")' <<< "$raw" >/dev/null 2>&1 || return 1
    jq -rs '[.[][] | .full_path // empty]
            | . as $all | .[] | select(. as $p | any($all[]; . as $q | $q != $p and ($p | startswith($q + "/"))) | not)' \
        <<< "$raw" 2>/dev/null | sort -u
}

# Settings section for a JSON array of settings fragments on stdin. A count of
# "off" covers only readable settings; unknown values are counted apart.
SETTING_FIELDS='["two_factor","push_rules","default_branch_protection","rulesets"]'
settings_headline() {
    jq -r --argjson f "$SETTING_FIELDS" '
      [.[] as $s | $f[] | $s[.].value // "unknown"] as $v
      | "\(length) \(if .[0].provider=="gitlab" then "group" else "org" end)(s): "
        + "\([$v[]|select(.=="off")]|length) setting(s) off, "
        + "\([$v[]|select(.=="unknown")]|length) unknown"'
}
render_settings() {
    jq -r --argjson f "$SETTING_FIELDS" '
      sort_by(.scope)[] as $s
      | "- \($s.scope)",
        ($f[] as $k | ($s[$k] // {value:"unknown", reason:"missing from the fragment"})
         | "  - \($k): \(.value)\(if .value=="unknown" then " (\(.reason // "no reason given"))"
                                   elif (.note|type)=="string" then " (\(.note))" else "" end)")'
}
settings_legend() {
    echo "Each setting is its value, \`off\`, or \`unknown\` with the reason it could not be read (no access, tier, or endpoint missing). \`unknown\` is never counted as \`off\`."
}
