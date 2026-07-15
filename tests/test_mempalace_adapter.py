"""Standalone tests for the MemPalace -> Notary adapter (stdlib only)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notary_memory_kit.mempalace_adapter import (  # noqa: E402
    _collapse_chunked_rows,
    _drawer_fact,
    _is_registry_row,
    _normalize_timestamp,
    build_snapshot,
    export_knowledge_graph,
    export_palace_drawers,
    main,
    synthesize_authorities,
)

# Live schema from MemPalace knowledge_graph._init_db (knowledge_graph.py:150).
KG_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'unknown',
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE triples (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL DEFAULT 1.0,
    source_closet TEXT,
    source_file TEXT,
    source_drawer_id TEXT,
    adapter_name TEXT,
    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subject) REFERENCES entities(id),
    FOREIGN KEY (object) REFERENCES entities(id)
);
"""


def build_fixture_kg(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(KG_SCHEMA)
    conn.executemany(
        "INSERT INTO entities (id, name, type) VALUES (?, ?, ?)",
        [
            ("ent-user", "The user", "person"),
            ("ent-docs-v2", "docs-v2", "project"),
            ("ent-docs-v3", "docs-v3", "project"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO triples
            (id, subject, predicate, object, valid_from, valid_to,
             confidence, source_closet, source_file, source_drawer_id,
             adapter_name, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            # Superseded pair: t1 closed exactly where t2 opens.
            (
                "t1", "ent-user", "migration_target", "ent-docs-v2",
                "2026-05-01T09:00:00Z", "2026-06-01T00:00:00Z",
                0.9, None, "convos/session-a.jsonl", None,
                "claude_code", "2026-05-01T09:05:00Z",
            ),
            (
                "t2", "ent-user", "migration_target", "ent-docs-v3",
                "2026-06-01T00:00:00Z", None,
                0.8, None, "convos/session-b.jsonl", None,
                "claude_code", "2026-06-01T00:05:00Z",
            ),
            # Unrelated triple with no provenance; extracted_at in
            # SQLite CURRENT_TIMESTAMP form (space-separated, naive).
            (
                "t3", "ent-user", "prefers", "ent-docs-v2",
                None, None,
                1.0, None, None, None,
                None, "2026-06-02 10:00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()


def test_registry_predicate_matches_all_sentinel_forms() -> None:
    # The three markers from MemPalace's sync._is_registry_row.
    assert _is_registry_row({"ingest_mode": "registry"})
    assert _is_registry_row({"room": "_registry"})
    assert _is_registry_row({}, "_reg_abc123")
    assert not _is_registry_row({"room": "notes", "ingest_mode": "convos"}, "d-1")

    # _drawer_fact skips every form, including legacy metadata.
    assert _drawer_fact("d-1", "x", {"room": "_registry"}) is None
    assert _drawer_fact("_reg_abc", "x", {"wing": "w", "room": "r"}) is None

    print("PASS: full registry sentinel predicate")


def test_chunked_drawers_collapse_to_one_logical_fact() -> None:
    rows = [
        # Out-of-order chunks of one oversized MCP drawer.
        ("d-1_chunk_000001", "world", {"parent_drawer_id": "d-1", "chunk_index": 1,
                                       "wing": "w", "room": "r", "added_by": "mcp"}),
        ("d-1_chunk_000000", "hello ", {"parent_drawer_id": "d-1", "chunk_index": 0,
                                        "wing": "w", "room": "r", "added_by": "mcp"}),
        # A plain single-row drawer.
        ("d-2", "standalone", {"wing": "w", "room": "r", "added_by": "mcp"}),
        # An oversized diary entry (parent_entry_id linkage).
        ("e-1_chunk_000000", "dear ", {"parent_entry_id": "e-1", "chunk_index": 0,
                                       "wing": "w", "room": "diary", "agent": "writer"}),
        ("e-1_chunk_000001", "diary", {"parent_entry_id": "e-1", "chunk_index": 1,
                                       "wing": "w", "room": "diary", "agent": "writer"}),
    ]

    collapsed = {fact_id: (document, meta) for fact_id, document, meta in _collapse_chunked_rows(rows)}

    assert set(collapsed) == {"d-1", "d-2", "e-1"}, set(collapsed)
    assert collapsed["d-1"][0] == "hello world"
    assert collapsed["e-1"][0] == "dear diary"
    assert collapsed["d-2"][0] == "standalone"
    # chunk_index must not leak into the merged logical metadata.
    assert "chunk_index" not in collapsed["d-1"][1]

    fact = _drawer_fact("e-1", *collapsed["e-1"])
    assert fact is not None and fact["fact_id"] == "e-1" and fact["agent_id"] == "writer"

    print("PASS: chunked drawers collapse")


def test_knowledge_graph_export() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge_graph.sqlite3"
        build_fixture_kg(db_path)
        facts = export_knowledge_graph(db_path)

    by_id = {fact["fact_id"]: fact for fact in facts}
    assert len(facts) == 3, f"expected 3 facts, got {len(facts)}"

    t1, t2, t3 = by_id["t1"], by_id["t2"], by_id["t3"]

    # Content resolves entity names through the entities table.
    assert t2["content"] == "The user migration_target docs-v3", t2["content"]

    # Overwrite lineage reconstructed from the shared temporal boundary.
    assert t2["overwrite_of"] == "t1", t2["overwrite_of"]
    assert t1["overwrite_of"] is None
    assert t3["overwrite_of"] is None

    # All triples are durable knowledge: lifecycle is permanent whether
    # or not the validity interval is closed — supersession is carried
    # by overwrite_of, not by the lifecycle class.
    assert t2["lifecycle"] == "permanent"
    assert t1["lifecycle"] == "permanent"

    # Provenance maps only what exists; absent fields stay empty.
    assert t2["agent_id"] == "claude_code"
    assert t2["session_id"] == "convos/session-b.jsonl"
    assert t2["timestamp"] == "2026-06-01T00:00:00Z"
    assert t2["surface"] == "migration_target"
    assert t2["confidence"] == 0.8
    assert t3["agent_id"] == ""
    assert t3["session_id"] == ""
    # extracted_at fallback, normalized from "2026-06-02 10:00:00".
    assert t3["timestamp"] == "2026-06-02T10:00:00Z", t3["timestamp"]

    print("PASS: knowledge graph export")


def test_timestamp_normalization() -> None:
    # KG values are UTC (assume_utc=True): date-only valid_from and
    # SQLite CURRENT_TIMESTAMP (space separator, naive) gain Z.
    assert _normalize_timestamp("2026-05-01", assume_utc=True) == "2026-05-01T00:00:00Z"
    assert _normalize_timestamp("2026-06-02 10:00:00", assume_utc=True) == "2026-06-02T10:00:00Z"
    # Drawer values are LOCAL naive (datetime.now().isoformat()); they
    # keep the T separator but must not be falsely declared UTC.
    assert _normalize_timestamp("2026-06-02 10:00:00") == "2026-06-02T10:00:00"
    assert _normalize_timestamp("2026-06-02T10:00:00.123456") == "2026-06-02T10:00:00.123456"
    # Already-normalized values are preserved.
    assert _normalize_timestamp("2026-05-01T09:00:00Z") == "2026-05-01T09:00:00Z"
    # Offset forms stay aware (only +00:00 is folded to Z).
    assert _normalize_timestamp("2026-05-01T09:00:00+02:00") == "2026-05-01T09:00:00+02:00"
    # Empty/missing and unparseable values pass through for validation.
    assert _normalize_timestamp(None) == ""
    assert _normalize_timestamp("") == ""
    assert _normalize_timestamp("not-a-date") == "not-a-date"

    print("PASS: timestamp normalization")


def test_drawer_facts_skip_sentinels_and_keep_diary_authors() -> None:
    # Registry sentinel (convo_miner._register_file) must be skipped,
    # exactly as MemPalace's own sync path does.
    sentinel = _drawer_fact(
        "sentinel-1",
        "[registry] convos/session-a.jsonl",
        {
            "wing": "projects",
            "room": "_registry",
            "source_file": "convos/session-a.jsonl",
            "added_by": "mempalace",
            "filed_at": "2026-06-02T10:00:00",
            "ingest_mode": "registry",
        },
    )
    assert sentinel is None

    # MCP diary drawers record the author under "agent", not "added_by".
    diary = _drawer_fact(
        "diary-1",
        "Shipped the adapter today.",
        {
            "wing": "personal",
            "room": "diary",
            "agent": "diary-writer",
            "filed_at": "2026-06-02T10:00:00",
        },
    )
    assert diary is not None
    assert diary["agent_id"] == "diary-writer"
    assert diary["surface"] == "personal/diary"
    # Local-naive drawer time stays naive (no fabricated Z).
    assert diary["timestamp"] == "2026-06-02T10:00:00"

    # added_by wins when both keys exist.
    both = _drawer_fact(
        "d-2",
        "content",
        {"added_by": "miner", "agent": "other", "wing": "w", "room": "r"},
    )
    assert both is not None and both["agent_id"] == "miner"

    print("PASS: drawer sentinel skip + diary author fallback")


def test_synthetic_authorities_are_opt_in_and_marked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge_graph.sqlite3"
        build_fixture_kg(db_path)

        snapshot = build_snapshot(db_path, None)
        assert snapshot["authorities"] == [], "authorities must default to empty"

        snapshot = build_snapshot(db_path, None, with_synthetic_authorities=True)

    authorities = snapshot["authorities"]
    assert len(authorities) == 1, authorities  # only 'claude_code'; empty agent skipped
    authority = authorities[0]
    assert authority["agent_id"] == "claude_code"
    assert authority["allowed_surfaces"] == ["migration_target"]
    assert authority["can_overwrite"] is True
    assert authority["synthetic"] is True

    print("PASS: synthetic authorities opt-in")


def test_drawer_export_requires_chromadb() -> None:
    if "chromadb" in sys.modules or _chromadb_available():
        print("SKIP: chromadb installed; error-path test not applicable")
        return
    try:
        export_palace_drawers(Path("."))
    except RuntimeError as exc:
        assert "chromadb" in str(exc)
        print("PASS: drawer export fails clearly without chromadb")
    else:
        raise AssertionError("expected RuntimeError without chromadb")


def _chromadb_available() -> bool:
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return False
    return True


def test_cli_writes_notary_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge_graph.sqlite3"
        output = Path(tmp) / "out" / "snapshot.json"
        build_fixture_kg(db_path)

        code = main(["--kg", str(db_path), "--output", str(output)])
        assert code == 0
        snapshot = json.loads(output.read_text())

    assert set(snapshot) == {"facts", "authorities"}
    assert len(snapshot["facts"]) == 3
    required = {
        "fact_id", "content", "agent_id", "session_id", "timestamp",
        "surface", "lifecycle", "confidence", "overwrite_of",
    }
    for fact in snapshot["facts"]:
        missing = required - set(fact)
        assert not missing, f"fact {fact.get('fact_id')} missing {missing}"

    print("PASS: CLI export")


def test_export_scores_under_notary_when_available() -> None:
    notary_repo = Path(__file__).resolve().parents[2] / "Notary"
    runner = notary_repo / "benchmark" / "runner.py"
    if not runner.exists():
        print("SKIP: Notary checkout not available for scoring")
        return

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge_graph.sqlite3"
        output = Path(tmp) / "snapshot.json"
        build_fixture_kg(db_path)
        assert main(["--kg", str(db_path), "--output", str(output)]) == 0

        result = subprocess.run(
            [sys.executable, str(runner), str(output)],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(notary_repo), "PATH": "/usr/bin:/bin"},
        )
    assert result.returncode == 0, result.stderr
    assert "Facts analyzed:" in result.stdout

    print("PASS: Notary runner accepts the export")


def run_all() -> None:
    test_timestamp_normalization()
    test_drawer_facts_skip_sentinels_and_keep_diary_authors()
    test_registry_predicate_matches_all_sentinel_forms()
    test_chunked_drawers_collapse_to_one_logical_fact()
    test_knowledge_graph_export()
    test_synthetic_authorities_are_opt_in_and_marked()
    test_drawer_export_requires_chromadb()
    test_cli_writes_notary_snapshot()
    test_export_scores_under_notary_when_available()
    print("PASS: mempalace adapter tests")


if __name__ == "__main__":
    run_all()
