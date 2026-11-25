# ci_includes: GitLab CI includes and the merged configuration (#766, #1180)

Reference for `lib/ci/ci_includes.py` (with `lib/ci/ci_include_items.py`, the `include:` parser, and
`lib/ci/ci_merged_view.py`, the line view). The module docstring has the summary.

## The merged configuration (#1180)

`merged_config(repo_root, ghsync_root)` returns a `MergedConfig`, or None without a readable
`.gitlab-ci.yml`. It is built from `resolve_includes`, whose `Resolution.slots` list GitLab's
merge order and whose `Resolution.root` holds the repository's own file. An unread include's
slot carries the item as written (`Slot.include`, `<kind>: <source>`), so a reader can tell which
include it is: the secrets guard counts only the Secret-Detection include's own slot (#1204).

### Precedence

| Step | Rule (GitLab docs: `include`, `extends`, `default`) |
|------|------|
| Include merge | Included files merge in the order listed; a nested include merges before the file that includes it; the root file merges last. A later definition of a key wins. Two maps deep-merge; a list or scalar replaces. |
| `extends:` | Read from the merged job. Parents in order, the last winning, then the job's own keys; the same deep merge. Up to 11 levels. |
| `default:` | A job takes each `DEFAULT_KEYS` key it does not set after `extends:`, unless `inherit: default:` is `false` or a list without it. The deprecated top-level `image`, `services`, `cache`, `before_script` and `after_script` count as `default:` keys. |

### Shape

| Field | Meaning |
|-------|---------|
| `MergedConfig.sources` | The root file text (0), then each `Resolution.files` text. Never published. |
| `MergedConfig.jobs` | Every top-level entry that is not in `GLOBAL_KEYS`, hidden templates included, as a `MergedJob`. |
| `MergedConfig.default` / `.workflow` | `default:` (with the legacy top-level keys) and `workflow:`, merged the same way. |
| `MergedConfig.open` | An include this run could not read (or a top-level name this reader cannot parse, such as a `$[[ ]]` name) may define any job. |
| `MergedJob.defs` | Each file's definition of the job (`JobDef`: source, line range, keys), lowest precedence first. |
| `MergedJob.keys` | Per key, the `KeyDef` chain (source, line range, column) after include merge, `extends:` and `default:`, lowest precedence first. The last wins; consecutive maps deep-merge. `extends` itself is dropped once applied. |
| `MergedJob.extended` | The jobs `extends:` reached, nearest first. |
| `MergedJob.inherited` | The keys taken from `default:`. |
| `MergedJob.unresolved` | Set when the job's keys are not known in full; read the job as not measured. |
| `MergedJob.open` | An include this run could not read may also define this job. A strict reader treats it as unresolved. |

### Unresolved reasons

| Code | Cause |
|------|-------|
| `conditional_include` | A definition comes from an include with `rules:` (or one nested under it): GitLab may leave it out. |
| `include_order` | A file included twice (where GitLab places it is not read), or a job named like one in the toolkit's gate template (`TOOLKIT_GATE_JOBS`). |
| `unreadable` | An anchor merge (`<<: *a`), an alias value, a `$[[ ]]` interpolation, an inline job value, a duplicate key, a child line this reader cannot parse, or an `extends:` value that is not a name or list. |
| `extends_missing` | An `extends:` parent no file read defines (it may sit in an include not read). |
| `extends_cycle` / `extends_depth` | A cycle, or more than 11 levels. |
| `inherit` | An `inherit:` this reader cannot read, where the job would take a `default:` key. |
| `default_unresolved` | The `default:` key the job takes is itself unresolved, or set both in `default:` and at the top level. |

### For the follow-on readers

- `ci_checks` (#1180) reads jobs through `ci_merged_view.merged_view`: each merged job (defined
  in more than one file, using `extends:`, or taking `before_script:` / `after_script:` from
  `default:`) is blanked in its files and rendered once with its effective keys, so masking and
  triggers read the merged job. `unresolved` jobs read with unknown triggers. `open` is not
  applied there: #766 already reads a missing check as `included_jobs`.
- A reader that must never credit an unread override (the secrets guard, #1204; model-backed
  jobs, #1249) checks `unresolved is None and not open` before reading a job's keys, and reads
  a key's value from `keys[key][-1]` (or the deep merge of the chain for a map). The secrets
  guard (`lib.secrets_guard_gitlab`) reads `merged_view` and holds a merged job that fails the
  check; it applies `open` only while an include that could hold a job was not read, since an
  unread GitLab `template:` defines only the template's own jobs.
- What counts as the toolkit's gate (#1179): `is_toolkit_gate` matches only the exact `remote:`
  URL; a `component:` settles only through `is_toolkit_gate_text`, below. Its slot defines
  `TOOLKIT_GATE_JOBS`, so a same-named job stays `include_order`.

## Include resolution (#766)

GitLab CI includes resolved offline, from the ghsync root (issue #766).

``ci_checks`` (#698) and ``review_automation`` (#696) read a repository's own
``.gitlab-ci.yml``. On an estate where the jobs come from shared templates, the
test job or the AI review step lives in another project. This module reads the
top-level ``include:`` of ``.gitlab-ci.yml`` and, where the included file is on
disk, returns its text so those scans read the included jobs too:

- ``local:`` (or a bare path item): a file of the same checkout. In the
  repository itself it is read from the work tree when git tracks it (an
  uncommitted fragment is ``file_missing``), as ``.gitlab-ci.yml`` is; in
  a template it is read at the template's resolved commit. A glob is not
  expanded (``glob``).
- ``project: <path>`` with ``file:`` (a scalar or a list) and ``ref:``: the
  clone ``<root>/<path>/<last segment>-main`` of the ghsync root passed as
  ``--ghsync-root`` (``fleet.py`` passes its ROOT). Each path segment matches
  ignoring case, as GitLab paths do, so ``Grp/CI-Templates`` finds the clone
  ``grp/ci-templates`` on every OS; two entries differing only in case are
  ambiguous (``not_cloned``) (#859). The file is read with
  ``git show`` at ``ref`` when the clone holds it (a branch, tag or
  ``origin/<ref>``; ``ref_basis: ref``). A pin the clone does not hold, or one
  that is not a plain ref name (a CI variable, an option-like value), is
  ``ref_missing``: reading HEAD instead could credit, or deny, the wrong
  version. No ``ref:`` reads HEAD (``ref_basis: head``), the project's default
  branch in a ghsync clone.
- ``component: <host>/<project path>/<name>@<version>``: the project
  ``<project path>`` resolved as above, file ``templates/<name>.yml`` or
  ``templates/<name>/template.yml``, at ``<version>`` when the clone holds it.
- ``template:`` and ``remote:`` name files offline reading cannot hold
  (``not_offline``).
- **The toolkit's own regression gate** (#1152) is ``toolkit_gate``: a ``remote:`` of
  exactly ``https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/<ref>/templates/assess-gate.yml``
  (the include the template documents), or a ``component:`` whose file, read from its clone as
  above, runs what the toolkit ships: after the ``spec:`` header's ``---``, its lines (blank and
  comment lines dropped) equal those of ``GATE_TEMPLATE`` (``templates/assess-gate.yml`` at the
  repository root). Input defaults may differ, as a mirror pins its own version and clone URL.
  A component's name is no evidence (#1179): any host or group can publish an
  ``ai-native-toolkit/assess-gate`` that runs tests. One with no clone (``no_root``,
  ``not_cloned``) or a pin the clone lacks (``ref_missing``) stays unread, so the test signal
  reads null, never a false 0. One whose file differs (another job, an added
  ``before_script:``, a gate from another release) is ``resolved`` and read like any
  template, as is every component in a build without ``GATE_TEMPLATE`` (a standalone copy):
  a file that runs tests then reads true, and the gate's own job reads null, since its
  ``$[[ inputs ]]`` lines are ``unreadable`` in the merge. That template's one job runs the AI-readiness gate and never the
  project's tests, so it does not leave the test signal unknown. It counts as read
  (``Resolution.unresolved`` and ``ci_checks.unresolved_includes`` skip it) with no file
  and no ``ref_basis``. Every other ``remote:`` stays ``not_offline``.

Included files are read for their own ``include:`` in turn, to ``MAX_DEPTH``
levels (the repository's own include is depth 1) and at most ``MAX_FILES``
files; past either bound an include is ``depth_limit`` / ``file_limit``. A file
already read (a cycle, or the same file included twice) is not read again.
Reads never leave a clone: a project path or file with a ``..`` or empty
segment is ``bad_path``, a ref that could pass as a git option is not tried,
and a clone that resolves outside the root (a symlink) is ``not_cloned``.

Each include item gets one ``IncludeRecord``: ``kind``, ``id`` (the
``shared_tooling`` id: ``gitlab_project:<path>`` for a project,
``gitlab_component:<path>`` for a component; null for the other kinds), ``status`` (``resolved`` or the token
saying why not; an entry this reader cannot parse is kind ``unknown``, status ``unparsed``), ``ref_basis`` (``ref``, ``head``, ``worktree``; null when not
resolved) and ``depth``. An item is resolved only when every file it names was
read. ``ci_checks`` publishes the records as ``includes`` with the counts
``resolved_includes`` / ``unresolved_includes``; ``review_automation`` reads
the include entries of the resolved files. Template text is never published.

Deterministic for fixed checkouts: ``git show`` of committed content, records in
walk order (file order, each nested include after its parent), no network. Read only (``GIT_OPTIONAL_LOCKS=0``).

GitLab CI includes resolved offline (issue #766), read by `ci_checks` and
`review_automation`. `include_items(text)` parses the top-level `include:` of a GitLab CI
file into `IncludeItem(kind, source, files, ref, line)` (block list, single mapping,
flow list or mapping, bare strings; `file:` as a scalar, flow list or block list) and
says whether a section exists. `resolve_includes(repo_root, ghsync_root)` (cached per
argument pair) walks the repository's `.gitlab-ci.yml`: `local:` from the work tree
(`ref_basis: worktree`) or, inside a template, from the template's commit; `project:`
from the clone `<ghsync_root>/<path>/<last>-main` (segments matched ignoring case, #859) with `git show <commit>:<file>` at
`ref:` when the clone holds it (`ref_basis: ref`), at HEAD only with no `ref:` (`head`); a pin
the clone does not hold, or no plain ref name (a CI variable), is `ref_missing`; `component:
<host>/<project>/<name>@<v>` from `templates/<name>.yml` or `templates/<name>/template.yml`
of the clone of `<project>`; `template:` / `remote:` are `not_offline`. Nested includes are
walked to `MAX_DEPTH` (5) and `MAX_FILES` (50); a file already read is not read again (a
cycle). Reads stay inside the checkout or clone: `..` or empty segments are `bad_path`, a ref
that is not a plain ref name is never passed to git (`ref_missing`), a clone resolving
outside the root (a symlink) is `not_cloned`.

Resolved-include data shape (read by #746 opportunity insights and #796, the
`--platform` CI lint pass): `Resolution.files` holds `IncludedFile(text, depth, line)` for
the scans (`line` is the repository's own include line that led there; the text is never
published), and `Resolution.includes` one `IncludeRecord` per item, published as
`ci_checks.includes`:

| field | value |
|-------|-------|
| `kind` | `local`, `project`, `component`, `template`, `remote`, `unknown` (an `unparsed` entry: its kind is not known, so it never counts as `local`, #959) |
| `id` | `gitlab_project:<path>` / `gitlab_component:<path>` (lowercased, the `shared_tooling` id); null for the other kinds. Kept in counts-only output (ID rule) |
| `status` | `resolved`; `toolkit_gate` (the toolkit's own gate template, known to run no project tests, #1152); or why not: `no_root` (no `--ghsync-root`), `not_cloned`, `file_missing`, `not_offline`, `glob`, `bad_path`, `depth_limit`, `file_limit`, `unparsed` (an `include:` entry naming no item this reader parses, including an unquoted YAML tag, anchor or alias such as `!reference [x]`), `ref_missing` |
| `ref_basis` | `ref`, `head`, `worktree`; null unless resolved |
| `depth` | 1 for the repository's own include; nested records follow their parent |

An item is resolved only when every file it names was read. `ci_checks` adds the
`resolved_includes` / `unresolved_includes` counts; a consumer that needs "jobs this run
could not read" reads `unresolved_includes > 0`. The root reaches the scan as the registry
input `ghsync_root` (`run_setup.ghsync_root`: None in an interactive run; the headless
`assess_signals --ghsync-root`, which `fleet.py` passes as its ROOT unless
`--delete-after`). Tests: `tests/test_ci_includes.py`.
