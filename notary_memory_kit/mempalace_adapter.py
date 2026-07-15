"""
MemPalace → Notary evidence adapter.

Reads a MemPalace store and emits a Notary-format snapshot
({"facts": [...], "authorities": [...]}) suitable for
`python -m benchmark.runner` in the Notary repository.

Sources:
  - Knowledge graph (SQLite, stdlib only): every triple becomes one fact.
    Real confidence, temporal lifecycle, and overwrite lineage are
    reconstructed from the triples table.
  - Palace drawers (ChromaDB, optional): every drawer becomes one fact.
    Requires the `chromadb` package; skipped with an explicit error if
    it is not installed. No new dependency is added to this kit.

Mapping notes (kept deliberately honest):
  - MemPalace has NO write-authority model, so by default the snapshot
    contains an empty `authorities` list. Under Notary's default-deny
    stability semantics the export will score accordingly — that
    reflects the real governance posture, not an adapter defect.
    Pass --synthesize-authorities to emit one permissive synthetic
    WriteAuthority per observed agent (clearly marked "synthetic").
  - MemPalace stores no session identity. session_id is derived from
    the triple/drawer source_file when present; otherwise it stays
    empty and fails governance, as it should.
  - Knowledge-graph triples carry no explicit overwrite link. A
    triple's `overwrite_of` is reconstructed by matching its
    valid_from to the valid_to of a prior triple with the same
    (subject, predicate).
  - lifecycle: knowledge-graph triples are durable knowledge and all
    map to "permanent". A closed validity interval (valid_to set)
    means the fact was superseded, which the reconstructed
    overwrite_of already expresses — validity state is not a
    lifecycle class. Drawers are likewise "permanent".
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _row_value(row: sqlite3.Row, key: str) -> Any:
    try:
        return row[key]
    except IndexError:
        return None


def _triple_content(entities: Dict[str, str], row: sqlite3.Row) -> str:
    subject = entities.get(row["subject"], row["subject"])
    obj = entities.get(row["object"], row["object"])
    return f"{subject} {row['predicate']} {obj}"


def _reconstruct_overwrites(rows: List[sqlite3.Row]) -> Dict[str, str]:
    """Map triple id -> id of the triple it superseded.

    MemPalace's KnowledgeGraph.supersede() closes the old triple
    (valid_to = at) and opens the new one (valid_from = at) for the
    same (subject, predicate) in one transaction, but stores no
    explicit link. Matching those boundaries recovers the lineage.
    """
    by_key: Dict[tuple, List[sqlite3.Row]] = {}
    for row in rows:
        by_key.setdefault((row["subject"], row["predicate"]), []).append(row)

    overwrites: Dict[str, str] = {}
    for group in by_key.values():
        for row in group:
            valid_from = row["valid_from"]
            if not valid_from:
                continue
            for prior in group:
                if prior["id"] != row["id"] and prior["valid_to"] == valid_from:
                    overwrites[row["id"]] = prior["id"]
                    break
    return overwrites


def export_knowledge_graph(db_path: Path) -> List[Dict[str, Any]]:
    """Convert every knowledge-graph triple into a Notary fact dict."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        entities = {
            row["id"]: row["name"]
            for row in conn.execute("SELECT id, name FROM entities")
        }
        rows = list(conn.execute("SELECT * FROM triples ORDER BY rowid"))
    finally:
        conn.close()

    overwrites = _reconstruct_overwrites(rows)

    facts = []
    for row in rows:
        confidence = row["confidence"]
        facts.append({
            "fact_id": row["id"],
            "content": _triple_content(entities, row),
            "agent_id": _row_value(row, "adapter_name") or "",
            "session_id": row["source_file"]
                or _row_value(row, "source_drawer_id")
                or row["source_closet"]
                or "",
            "timestamp": row["valid_from"] or _row_value(row, "extracted_at") or "",
            "surface": row["predicate"],
            "lifecycle": "permanent",
            "confidence": confidence if confidence is not None else 1.0,
            "overwrite_of": overwrites.get(row["id"]),
        })
    return facts


def export_palace_drawers(
    palace_path: Path,
    collection_name: str = "mempalace_drawers",
) -> List[Dict[str, Any]]:
    """Convert every palace drawer into a Notary fact dict.

    Optional: requires the chromadb package (MemPalace's own storage
    backend); this kit does not depend on it.
    """
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "drawer export requires the 'chromadb' package (MemPalace's "
            "storage backend); install it or export the knowledge graph only"
        ) from exc

    client = chromadb.PersistentClient(path=str(palace_path))
    collection = client.get_collection(collection_name)

    facts = []
    offset = 0
    batch = 1000
    while True:
        result = collection.get(
            limit=batch,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        if not ids:
            break
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        for drawer_id, document, meta in zip(ids, documents, metadatas):
            meta = meta or {}
            wing = meta.get("wing", "")
            room = meta.get("room", "")
            surface = f"{wing}/{room}" if wing or room else ""
            facts.append({
                "fact_id": drawer_id,
                "content": document or "",
                "agent_id": meta.get("added_by", ""),
                "session_id": meta.get("source_file", ""),
                "timestamp": meta.get("authored_at") or meta.get("filed_at") or "",
                "surface": surface,
                "lifecycle": "permanent",
                "confidence": 1.0,
                "overwrite_of": None,
            })
        offset += len(ids)
    return facts


def synthesize_authorities(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One permissive WriteAuthority per observed agent.

    MemPalace has no authority model, so these records are synthetic
    and marked as such — they let the snapshot pass default-deny
    checks but prove nothing about real write governance.
    """
    surfaces_by_agent: Dict[str, set] = {}
    for fact in facts:
        agent_id = fact.get("agent_id") or ""
        if not agent_id:
            continue
        surfaces_by_agent.setdefault(agent_id, set())
        surface = fact.get("surface") or ""
        if surface:
            surfaces_by_agent[agent_id].add(surface)

    return [
        {
            "agent_id": agent_id,
            "allowed_surfaces": sorted(surfaces),
            "can_overwrite": True,
            "synthetic": True,
        }
        for agent_id, surfaces in sorted(surfaces_by_agent.items())
    ]


def build_snapshot(
    kg_path: Optional[Path],
    palace_path: Optional[Path],
    with_synthetic_authorities: bool = False,
    collection_name: str = "mempalace_drawers",
) -> Dict[str, Any]:
    facts: List[Dict[str, Any]] = []
    if kg_path is not None:
        facts.extend(export_knowledge_graph(kg_path))
    if palace_path is not None:
        facts.extend(export_palace_drawers(palace_path, collection_name))

    authorities = synthesize_authorities(facts) if with_synthetic_authorities else []
    return {"facts": facts, "authorities": authorities}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mempalace-adapter",
        description="Export a MemPalace store to the Notary snapshot format.",
    )
    parser.add_argument(
        "--kg",
        type=Path,
        help="path to MemPalace knowledge_graph.sqlite3",
    )
    parser.add_argument(
        "--palace",
        type=Path,
        help="path to the MemPalace palace directory (requires chromadb)",
    )
    parser.add_argument(
        "--collection",
        default="mempalace_drawers",
        help="palace collection name (default: mempalace_drawers)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/mempalace-notary-evidence.json"),
        help="where to write the Notary snapshot JSON",
    )
    parser.add_argument(
        "--synthesize-authorities",
        action="store_true",
        help="emit one permissive synthetic WriteAuthority per observed agent "
        "(MemPalace has no authority model; default is an empty list)",
    )
    args = parser.parse_args(argv)

    if args.kg is None and args.palace is None:
        parser.error("provide --kg and/or --palace")
    if args.kg is not None and not args.kg.exists():
        parser.error(f"knowledge graph not found: {args.kg}")
    if args.palace is not None and not args.palace.exists():
        parser.error(f"palace directory not found: {args.palace}")

    try:
        snapshot = build_snapshot(
            args.kg,
            args.palace,
            with_synthetic_authorities=args.synthesize_authorities,
            collection_name=args.collection,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Exported {len(snapshot['facts'])} facts and "
        f"{len(snapshot['authorities'])} authorities -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
