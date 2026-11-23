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
rule (path `""`) means no viewer writes documents outside the collections below. Each such
document would count against the document cap. The root rule also covers each viewer's
`data/users/` subtree, so a page that keeps per-viewer state there needs its own
`data/users/{self}` rule.

| Path | Read | Write |
|---|---|---|
| `""` (root) | `view` | `owner` |
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
says whether that count is within one artifact's limit. Sharding (#950) reads both.

For a stored document, the guard adds the whole stored size to this run's entry. A
republished run is therefore counted twice, which overstates the size and never
understates it. A synthetic hashed 10,000-repository fleet comes to 10,005 documents. That is over the
5,000 cap, so it is planned as shards (#950). Its
largest is the `runs` document, at about 211 KB, which is over the 80% warning: the
opportunity lists name every repository they lift.

### Batches

Writes are ordered `runs`, `units`, `repos`, each by document id. They are cut into
batches within the count and body limits. The writes carry no `if_version`. The
`ArtifactData` tool refuses a write to an existing document without one, so the publisher
reads the current versions and pins each write.

### Targets read-back

The page writes one `targets` document per Step target, and `/fleet` reads the collection
back before the next run (#948). `fleet.py` stays free of network calls, so the session
exports the collection to a JSON file and runs
`skills/fleet/scripts/fleet_targets_import.py`, which writes the targets file that
`fleet.py --targets` reads ([format](achievements.md#output-achievementsjson)). The logic
is `lib/fleet/fleet_targets_import.py`.

```bash
uv run skills/fleet/scripts/fleet_targets_import.py targets-export.json \
    --fleet-data out/fleet-data.json --levels levels.toml --out targets.toml
```

**Row contract.** The page writes to this shape.

- **Export.** A JSON list with one object per document. Only `doc_id` and `data` are read;
  the tool's other fields (version, timestamps) are ignored.
- **`doc_id`.** `{view}~{unitDocId}`: the view (`ownership`, `system` or `repo`), a `~`, then
  the unit's `fleet-data.json` key encoded as a [document id](#collections). A unit key
  keeps its view prefix, so `ownership/acme/payments` is
  `ownership~ownership~2Facme~2Fpayments`. A repository is `repo~acme~2Fsvc`.
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
text. A refused row never stops the import: the valid rows are still written.

**Output.** Only a known view, the unit id from the matched `fleet-data.json` entry and an
integer Step reach the file, so no text a viewer typed is copied into it. Targets are
sorted by view and unit, with LF line endings, and the same export and bundle give the
same bytes. The file is rewritten on every import, even with no valid rows, so a cleared
target does not linger. Exit 2 means the export, the bundle or the maturity model is
unreadable, or the model has no `[[steps]]` above Step 1.
