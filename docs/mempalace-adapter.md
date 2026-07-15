# MemPalace → Notary adapter

`notary_memory_kit/mempalace_adapter.py` converts a
[MemPalace](https://github.com/MemPalace/mempalace) store into the Notary
snapshot format (`{"facts": [...], "authorities": [...]}`) so it can be scored
with Notary's benchmark runner.

## Usage

```bash
# Knowledge graph only (stdlib, no extra dependencies):
python3 -m notary_memory_kit.mempalace_adapter \
  --kg ~/.mempalace/knowledge_graph.sqlite3 \
  --output out/mempalace-notary-evidence.json

# Include palace drawers (requires MemPalace's chromadb backend):
python3 -m notary_memory_kit.mempalace_adapter \
  --kg ~/.mempalace/knowledge_graph.sqlite3 \
  --palace ~/.mempalace/palace \
  --output out/mempalace-notary-evidence.json

# Score it:
PYTHONPATH=/path/to/notary python3 -m benchmark.runner out/mempalace-notary-evidence.json
```

## Field mapping

| Notary field | Knowledge-graph triple | Palace drawer |
|---|---|---|
| `fact_id` | `triples.id` | drawer id |
| `content` | `"<subject name> <predicate> <object name>"` (names resolved via `entities`) | document text |
| `agent_id` | `adapter_name` | `added_by` → `agent` (the MCP diary writer uses `agent`) |
| `session_id` | `source_file` → `source_drawer_id` → `source_closet` | `source_file` |
| `timestamp` | `valid_from` → `extracted_at` | `authored_at` → `filed_at` |
| `surface` | `predicate` | `"<wing>/<room>"` |
| `lifecycle` | `"permanent"` (all triples are durable knowledge) | `"permanent"` |
| `confidence` | `confidence` | `1.0` (drawers carry none) |
| `overwrite_of` | reconstructed: prior triple with the same `(subject, predicate)` whose `valid_to` equals this triple's `valid_from` (MemPalace's `supersede()` boundary) | none (drawer overwrites are destructive) |

## Honest-by-default governance mapping

MemPalace has **no write-authority model, no session identity, and no
explicit overwrite links**. The adapter maps only what actually exists:

- `authorities` defaults to an **empty list**. Under Notary's default-deny
  stability semantics the export scores accordingly — that reflects
  MemPalace's real governance posture, not an adapter defect.
- `--synthesize-authorities` emits one permissive `WriteAuthority` per
  observed agent, each marked `"synthetic": true`. Use it to separate
  format-compatibility questions from governance questions; it proves
  nothing about real write control.
- Missing provenance (e.g. a triple with no `source_file`) stays empty and
  fails governance, as it should.

## Timestamps

All exported timestamps are normalized to ISO-8601 with a `T` separator.
Knowledge-graph values are UTC (SQLite `CURRENT_TIMESTAMP`; date-only
`valid_from` becomes `T00:00:00Z`) and gain a `Z` suffix. Palace drawer
values (`filed_at`/`authored_at`) are written by MemPalace as **local,
timezone-naive** `datetime.now().isoformat()` — they are kept naive rather
than falsely declared UTC, because the writing host's offset is unknown at
export time.

## Skipped rows

Registry sentinels — bookkeeping rows MemPalace writes so zero-chunk source
files are not re-mined — are excluded from the export using MemPalace's own
full sentinel predicate (`room == "_registry"`, `ingest_mode == "registry"`,
or a drawer id starting with `_reg_`), so legacy or partially migrated
sentinels are excluded too. They are not memories.

## Chunked drawers

Content above MemPalace's `chunk_size` is stored as multiple physical rows
carrying `parent_drawer_id` (oversized diary entries carry
`parent_entry_id`) plus `chunk_index`. The adapter rejoins each group in
chunk order and exports **one** fact under the logical parent id, matching
how MemPalace's own get/list paths present them — so one memory is one
fact, not many partial ones.

## Limitations

- Drawer export requires the `chromadb` package (MemPalace's own storage
  backend). The kit does not add this dependency; without it the adapter
  works on the knowledge graph alone.
- Drawer-level overwrites in MemPalace are destructive (purge/upsert by
  deterministic id), so no `overwrite_of` lineage exists for drawers.
- The schema of record is MemPalace's live `knowledge_graph._init_db` DDL;
  `docs/schema.sql` in the MemPalace repository is stale (it omits the
  provenance columns).
