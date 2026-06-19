# Notary Memory Kit

Notary Memory Kit is a local companion toolkit for preparing governed agent-memory evidence for Notary-compatible benchmark review.

It helps evaluate, audit, and export synthetic memory evidence so governance behavior can be inspected with Notary.

## Status

This repository is an early public demo built around synthetic fixtures.
It is intended for review, experimentation, and evidence-shape discussion.

It is not packaged, released, or supported as production infrastructure.

## What It Does

- Ingests synthetic demo logs and session facts.
- Validates fact shape, confidence values, timestamps, and write-authority records.
- Reports authority-surface warnings for intentional policy violations.
- Searches local synthetic facts with a simple keyword flow.
- Exports Notary-compatible evidence JSON.
- Runs a reproducible synthetic demo against a pinned Notary revision.

## What It Is Not

- Not a memory store.
- Not a retrieval engine.
- Not a hosted service.
- Not a production memory system.
- Not a replacement for Notary.
- Not a package-published project.

## Demo

The included demo uses only synthetic `Atlas Docs Migration` data.

```bash
scripts/run_demo.sh
```

The demo writes generated local state under ignored paths:

- `demo/atlas-docs-migration/.notary-memory-kit/`
- `out/`

By default, the demo checks out a pinned Notary revision so benchmark behavior is reproducible across local and CI runs.

## Notary Revision Pin

`scripts/run_demo.sh` currently pins Notary to:

```text
6a7b73f8d15bca1b23be67f63446ffaac3b032af
```

This pin is for the synthetic demo only. It keeps local and CI scoring deterministic, but it is not a compatibility promise for other Notary revisions.

To test the demo against a different Notary commit:

```bash
NOTARY_COMMIT=<commit-sha> scripts/run_demo.sh
```

To test with an existing local Notary checkout:

```bash
NOTARY_REPO=/path/to/notary NOTARY_COMMIT=<commit-sha> scripts/run_demo.sh
```

## Manual Flow

```bash
python3 -m notary_memory_kit.cli ingest demo/atlas-docs-migration
python3 -m notary_memory_kit.cli facts demo/atlas-docs-migration
python3 -m notary_memory_kit.cli search demo/atlas-docs-migration "migration target"
python3 -m notary_memory_kit.cli export demo/atlas-docs-migration --notary out/notary-evidence.json
```

## Audit

Run the public-tree audit helper against the candidate tree:

```bash
scripts/audit_public_tree.sh .
```

The helper checks for generated outputs, unsupported package claims, and private-boundary terms. It is a guardrail, not a complete security review.

CI runs this audit, Python compilation, and the synthetic demo on pushes and pull requests.

## Evidence Schema

The current demo export shape is documented in [`docs/evidence-schema.md`](docs/evidence-schema.md).

## Boundary

Use this kit as a synthetic evidence-preparation companion for Notary. Keep real memory data, private implementation details, and generated outputs out of the reviewed tree.

## License

MIT, matching the public Notary repository.
