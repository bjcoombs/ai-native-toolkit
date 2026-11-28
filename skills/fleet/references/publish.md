## Database projection: `fleet-data.json` to artifact database writes

The live fleet artifact (#705) keeps its data in the artifact's `db` database.
`lib/fleet/fleet_publish.py` turns one run's [`fleet-data.json`](fleet-data.md) into the writes
the `ArtifactData` `batch` action takes. It is a pure function: it makes no network call,
and the same bundle, run id and stored sizes give the same bytes. The `/fleet` publish
step (#949) sends the batches.

### Collections

| Collection | One document per | Op | Body |
|---|---|---|---|
| `runs/{runId}` | run | `set` | The bundle without `views` and `repos`, plus `views` (`{view, units}` counts) and `counts` (`repos`, `units`). |
| `units/{unitKey}` | unit | `update`, or `set` when new | `{"history": {<runId>: <the unit without its key>}}` |
| `repos/{repoKey}` | repository | `update`, or `set` when new | `{"history": {<runId>: <the repository without its key>}}` |
| `targets/{view}~{unitDocId}` | target | never written here | Contributors write it from the page; [read back](#targets-read-back) before the next run. |

The unit and repository entries copy every field of the bundle. A field the bundle gains,
such as #945's `gate_blockers`, is published without a change to the projection.
`update` merges nested objects, so a run writes only its own `history.<runId>`.
Republishing the same bundle writes the same values again and changes nothing. A run id is
immutable once published. A merge cannot remove a field or an entry, so a corrected bundle
cannot replace what the run already holds. The caller passes what the run id holds now
(`previous`), and the publisher must always pass it, because the check sees only what
`previous` holds. A republish is refused, naming the documents, when its `runs` document or
any `history.<runId>` entry differs. It is also refused when it no longer writes a document
that the run id holds. Publish a corrected run under a new run id. `update` requires the
document to exist, so the caller passes the documents already stored, and a new one is
written with `set` and the same body.

**Document ids.** A database id is one path segment of `A-Z a-z 0-9 _ - . ~ : @ +`, 1 to
200 characters. Unit keys hold `/`, so every other character, and `~` and `.` too, is
written as `~` plus two hex digits per UTF-8 byte: `ownership/grp/sub` becomes
`ownership~2Fgrp~2Fsub`. A hashed key (`h:` plus 16 hex digits) is unchanged. The run id is
encoded the same way as a `history` key, so a same-day run `2026-11-20.2` is keyed
`2026-11-20~2E2`. `decode_doc_id` reverses the encoding. A key that encodes to more than 200 characters
refuses the whole publish. Only an unhashed key can be that long.

### Access rules

The artifact's `db` capability declares exactly these rules (`access_rules()`). The root
rule (path `""`) lets Contributors (`interact`) write (#949, `ROOT_WRITE`). At runtime contract
0.2.96 the page can only ask whether a viewer may write at the root (`user.can("data.write")`),
so this is how it offers target entry to Contributors and hides it from Viewers. The cost: a
Contributor can create stray documents outside the collections, and each counts against the
document cap. #1044 set the root to `owner` to prevent that; going back is the one constant.
The root rule also covers each viewer's `data/users/` subtree; the page keeps no state there.
With `runs`, `units` and `repos` at `write: owner`, only the artifact's own owner can publish
the data. An Editor, or a person granted Owner in the share menu, can publish page versions but
not the data.

| Path | Read | Write |
|---|---|---|
| `""` (root) | `view` | `interact` |
| `runs` | `view` | `owner` |
| `units` | `view` | `owner` |
| `repos` | `view` | `owner` |
| `targets` | `view` | `interact` |

### Privacy

A `--full` run is refused. A `--hash-paths` run is accepted, and it is the stricter mode.
Every string in the batch is checked against the counts-only rules by its
`fleet-data.json` pattern (`FLEET_DATA_RULES`, else `FLEET_DATA_KEPT`): a token must be a
short token, a dropped field must be absent, and a string with no rule is refused. Under
`--hash-paths` every key and path must be hashed and no web URL may appear. The error
names each pattern that failed.

### Size guard

| Limit | Value | Source |
|---|---|---|
| Bytes per document | 262,144 (256 KiB) | #692's database layout and the db capability's type definitions |
| Nesting levels per document | 32 | The db capability's type definitions |
| Documents per artifact | 5,000 | The db capability's type definitions, runtime contract 0.2.47. Contract 0.2.96 and #692 state 25,000. The smaller cap is the default, so a plan that fits under it fits under either. A publisher pinned to a contract that states more passes its own `Limits`. |
| Writes per `batch` call | 50 | The `ArtifactData` tool's `batch` action |
| Body per `batch` call | 1 MiB | The `ArtifactData` tool's `batch` action |

Each document's size is its body as compact JSON, plus the stored size for an `update`. A
document at 80% of the per-document limit is listed in `size.warnings`. One above the limit,
or nested too deep, stops the publish with an error naming it. `size.documents` is the
count after the publish (the documents already stored plus the new ones), and `size.fits`
says whether that count is within one artifact's limit. The [shard plan](#sharded-fleets) reads both.

For a stored document, the guard adds the whole stored size to this run's entry. A
republished run is therefore counted twice, which overstates the size and never
understates it. A synthetic hashed 10,000-repository fleet comes to 10,005 documents. That is over the
5,000 cap. Its largest is the `runs` document, at about 211 KB, which is over the 80% warning:
the opportunity lists name every repository they lift. Spread over three top-level units, the
same fleet is planned as three shards of about 3,340 documents each, and each shard's `runs`
document lists only its own repositories.

### Batches

Writes are ordered `runs`, `units`, `repos`, each by document id. They are cut into
batches within the count and body limits. The writes carry no `if_version`. The
`ArtifactData` tool refuses a write to an existing document without one, so the publisher
reads the current versions and pins each write.

### Targets read-back

The page writes one `targets` document per Step target, and `/fleet` reads the collection
back before the next run (#948). `fleet.py` stays free of network calls, so the session
exports the collection to a JSON file and runs
`scripts/fleet_targets_import.py`, which writes the targets file that
`fleet.py --targets` reads ([format](achievements.md#output-achievementsjson)). The logic
is `lib/fleet/fleet_targets_import.py`.

<!-- chat-skip:start -->
```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/fleet_targets_import.py" targets-export.json \
    --fleet-data out/fleet-data.json --levels levels.toml --out targets.toml
```
<!-- chat-skip:end -->

**Row contract.** The page writes to this shape.

- **Export.** A JSON list with one object per document. Only `doc_id` and `data` are read;
  the tool's other fields (version, timestamps) are ignored.
- **`doc_id`.** `{view}~{unitDocId}`: the view (`ownership`, `system` or `repo`), a `~`, then
  the unit's `fleet-data.json` key encoded as a [document id](#collections). A unit key
  keeps its view prefix, so `ownership/acme/payments` is
  `ownership~ownership~2Facme~2Fpayments`. A repository is `repo~acme~2Fsvc`. The whole id
  is at most 200 characters, so a unit whose encoded key is longer than 200 less the view
  and the `~` cannot hold a target, and the page offers no target control for it
  (`target_doc_id` refuses it). Only an unhashed key can be that long.
- **`data`.** `{"step": <2 to the top Step>}`, plus an optional `note` string of at most
  280 characters. The note is never copied into the targets file.

**Untrusted input.** Viewers write these rows. Each row becomes a target or is refused
with one reason, checked in this order:

| Reason | The row |
|---|---|
| `malformed_row` | is not an object, has no string `doc_id`, or its `data` is not an object |
| `bad_doc_id` | has no `~` separator, or a key that is not the canonical encoding |
| `unknown_view` | names a view other than `ownership`, `system` or `repo` |
| `unknown_field` | has a `data` key other than `step` and `note` |
| `string_too_long` | has a `note` that is not a string of at most 280 characters |
| `step_out_of_range` | has a `step` that is not a whole number from 2 to the top Step |
| `hashed_key` | names a hashed key. `fleet.py --targets` matches the run's plain unit ids, so a `--hash-paths` publish cannot be read back. |
| `unknown_unit` | names a key that is not a unit of that view, or a repository, in `--fleet-data` |
| `duplicate` | is one of two or more rows for the same unit (all of them are refused) |

Refusals go to stderr as `row N: <reason>`. The script never echoes a row's id or
text. A refused row never stops the import: the valid rows are still written. When the
export has rows and none is accepted, the import stops instead (exit 2) and leaves the
targets file as it was. This most often means `--fleet-data` is the bundle of another run,
or of a `--hash-paths` run.

**Output.** Only a known view, the unit id from the matched `fleet-data.json` entry and an
integer Step reach the file, so no text a viewer typed is copied into it. Targets are
sorted by view and unit, with LF line endings, and the same export and bundle give the
same bytes. The file is rewritten on every import, and an empty export writes it with no
targets, so a cleared target does not linger. Exit 2 means the export, the bundle or the
maturity model is unreadable, the model has no `[[steps]]` above Step 1, or no row of a
non-empty export was accepted. On exit 2 the targets file is not touched.

### Publishing the page

The `/fleet` session publishes ([SKILL.md](../SKILL.md#publish-the-fleet-page-opt-in)).
`scripts/fleet_publish_plan.py plan` writes everything it sends into `out/publish/`
(`lib/fleet/fleet_publish_session.py`, `lib/fleet/fleet_page_live.py`). Nothing in it, or in
the tests, publishes.

| File | What it is |
|---|---|
| `page.html` | `fleet-view.html` with a database source. |
| `capabilities.json` | `{"db": {"rules": [...]}}`, the rules above. No `user` declaration: the page only asks `can("data.write")`, which needs none, and it stores no viewer id. |
| `batch-NNN.json` | The `ArtifactData` `batch` writes, in order, each pinned with `if_version` when its document exists. |
| `plan.json` | The run id, the mode (`hashed` or `counts_only`), whether targets are on, the runtime contract (`0.2.96`, 25,000 documents) and the size report. |

**Modes.** A `--full` run is refused. The default needs a hashed run. A plain counts-only run
is planned only with `--allow-names`, since its documents and target ids carry repository and
unit names. Only that mode turns on target entry: the read-back matches plain unit names, so
a hashed page shows its data and says why it offers no targets.

**The page's source.** The page draws from its embedded data at once, then lights up when the
database answers:

- It subscribes once to `targets`. A row counts only when its id is one of the page's own
  target ids and its `step` is a whole number from 2 to the top Step. The page then shows that
  target as set, with its progress to come after the next run.
- It offers target entry only when the database is present, the page carries target ids and
  `can("data.write")` is `true`. A Viewer, or a viewer the platform says nothing about, sees
  none.
- A target is written as `{"step": N}` to `targets/<target id>`, or deleted to clear it. The id
  comes from the page's own data, never from what a viewer types, and must match
  `(ownership|system)~` plus id characters, at most 200 characters in all: the same limit the
  [row contract](#targets-read-back) sets, so every target id the page carries is one it can write.
- For the unit shown, it reads `units/<doc id>` once and lists each published run's lowest Step.

**Strays.** Because Contributors can write at the root, a document can appear outside what the
fleet writes. The planner counts every document of an exported collection other than `runs`,
`units`, `repos` and `targets`, and every `targets` document whose id is not a target id. It
reports them in `plan.json` (`strays`) and on stderr, and adds them and the targets to the
document count. The database tool lists one named collection at a time, so a stray in a
collection nobody exported is not seen.

**Stored state.** The planner reads the `runs`, `units` and `repos` exports for three things:
each document's size (the size guard), its version (the `if_version` pin) and what this run
id already holds (`previous`, so a changed republish is refused). `publish.json` in the
fleet's history directory for the org keeps the artifact URL and the runs published to it, as
`{"url": ..., "runs": [...]}`. `fleet_publish_plan.py url` prints the URL, or exits 1 before the
first publish; `record` adds a run. A `publish.json` of another shape is refused, never
overwritten, and `record` refuses a second artifact URL for the same org.

### Sharded fleets

A run over one artifact's limits is published as one artifact per top-level unit of the
ownership view, plus an index (#950). `lib/fleet/fleet_shard.py` plans it; it is pure and makes
no network call.

- **When.** The bundle is projected whole first. If it fits (every document within the size and
  depth limits, and the document count within the per-artifact limit), it stays one artifact and
  has no index. A fleet whose `shards.json` already records shards stays sharded.
- **Top-level units.** The children of the ownership view's roots. A repository goes to the
  shard of the top-level unit above its ownership unit. A repository with no ownership unit, or
  placed at a root, goes to one `unassigned` shard.
- **A shard's data.** Its repositories; the top-level unit, the units below it and the units
  above its repositories (so the root is in every shard); and the system units above its
  repositories. `opportunities.biggest_lift.repos` and `opportunities.shared_templates[].repos`
  keep only the shard's repositories, while their counts stay the fleet's.
- **Size guard per shard.** Each shard is projected on its own against its own stored
  documents, so the 80% warning and the refusal above the limit apply per shard and name the
  shard and the document. A shard with more documents than one artifact holds is refused,
  naming the unit and its estimate: one top-level unit is never split further.
- **The index.** One static page (`index.html`, no database) with, per shard, its label, kind,
  lowest Step, "N of M measured" and a link to its artifact, then the shards no longer in the
  fleet, still linked. It holds no repository document.
- **A shard's page.** The fleet page cut to the shard's units and repositories, without the
  fleet-wide front screen and pictures. It opens on its top-level unit, links to the index, and
  starts the drill-down breadcrumb at the index. In the system view it shows only its own
  repositories and says how many of the system's repositories each other shard holds, with a
  link to that shard. Only `https://claude.ai/` URLs are linked.
- **Registry.** `shards.json` in the fleet's history directory for the org holds
  `{"index": <url or null>, "shards": {<shard key>: <url>}}`. A new top-level unit is a new shard.
  A unit that leaves the fleet keeps its artifact and is listed on the index as no longer in the
  fleet. Nothing removes an entry or deletes a shard artifact.
- **Targets.** Each shard page writes targets to its own `targets` collection. Export each
  shard's and pass every export to one `fleet_targets_import.py` call, which merges them into
  one targets file. A unit targeted in two shards (a system spanning them) is refused as a
  duplicate.

<!-- chat-skip:start -->
The session, in order:

1. Run `fleet_publish_plan.py shards out "<history>/<org>"`. It prints `index <url or new>`, then
   `<folder> <url or new>` per shard. For each shard with a URL, export its collections into
   `exports/<folder>/`, as for one artifact.
2. Run `plan` with `--org-dir "<history>/<org>"`. It writes `out/publish/plan.json`, whose
   `steps` list the pages in publish order (the index, then each shard), `index.html`, and per
   shard `out/publish/shards/<folder>/` with `page.html`, `capabilities.json`, `plan.json` and
   its batches.
3. Publish the index page with no capabilities. Publish each shard's `page.html` with its
   `capabilities.json` and contract `0.2.96`, then send its batches in order. Pass the recorded
   URL for any step that has one.
4. Record each new URL: `fleet_publish_plan.py record-shard "<history>/<org>" --url URL --index`,
   or `--shard KEY` with the key the step names.
5. If `plan.json` says `links_complete` is false, a page went out without a link that now
   exists. Run `plan` again with the same arguments and republish only the pages; do not send
   the batches again.
<!-- chat-skip:end -->

An artifact published as one artifact before the fleet outgrew it is not moved to shards
automatically: the shard plan publishes new artifacts and leaves the old one as it was.
