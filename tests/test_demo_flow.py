from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from notary_memory_kit.cli import validate_authorities, validate_facts


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> None:
    export_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out/notary-evidence.json")
    expected_facts = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    expected_authorities = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    warning_fact = sys.argv[4] if len(sys.argv) > 4 else "atlas-f005"
    warning_surface = sys.argv[5] if len(sys.argv) > 5 else "project_scope"
    unauthorized_fact_id = sys.argv[6] if len(sys.argv) > 6 else "atlas-f005"
    unauthorized_agent = sys.argv[7] if len(sys.argv) > 7 else "builder"
    overwritten_fact_id = sys.argv[8] if len(sys.argv) > 8 else "atlas-f001"
    correction_fact_id = sys.argv[9] if len(sys.argv) > 9 else "atlas-f004"
    correction_agent = sys.argv[10] if len(sys.argv) > 10 else "reviewer"
    corrected_fact_id = sys.argv[11] if len(sys.argv) > 11 else "atlas-f006"

    if not export_path.exists():
        fail(f"missing export: {export_path}")

    data = json.loads(export_path.read_text(encoding="utf-8"))
    facts = data.get("facts", [])
    authorities = data.get("authorities", [])
    authority_audit = data.get("authority_audit", [])

    if len(facts) != expected_facts:
        fail(f"expected {expected_facts} facts, got {len(facts)}")
    if len(authorities) != expected_authorities:
        fail(f"expected {expected_authorities} authorities, got {len(authorities)}")
    if not any(warning_fact in issue and warning_surface in issue for issue in authority_audit):
        fail(f"expected {warning_fact} surface-authority audit warning, got {authority_audit}")

    serialized = json.dumps(data, ensure_ascii=False)
    blocked_terms = [
        "A" + "SMR",
        "Trace" + "Memory",
        "trace" + "memory",
        "/home/",
        "." + "open" + "claw",
        "Open" + "Claw",
        "Her" + "mes",
        "shared" + "-agents",
    ]
    for term in blocked_terms:
        if term in serialized:
            fail(f"blocked term leaked into export: {term}")

    by_id = {fact["fact_id"]: fact for fact in facts}
    unauthorized = by_id.get(unauthorized_fact_id)
    if not unauthorized:
        fail(f"missing {unauthorized_fact_id} unauthorized overwrite fixture")
    if unauthorized.get("agent_id") != unauthorized_agent or unauthorized.get("overwrite_of") != overwritten_fact_id:
        fail(f"{unauthorized_fact_id} does not model {unauthorized_agent} overwriting {overwritten_fact_id}")
    correction = by_id.get(correction_fact_id)
    if not correction:
        fail(f"missing {correction_fact_id} authorized correction fixture")
    if correction.get("agent_id") != correction_agent or correction.get("overwrite_of") != corrected_fact_id:
        fail(f"{correction_fact_id} does not model {correction_agent} correcting {corrected_fact_id}")

    for fact in facts:
        evidence_path = fact.get("evidence_path", "")
        if evidence_path.startswith("/") or ".." in evidence_path:
            fail(f"unsafe evidence path for {fact.get('fact_id')}: {evidence_path}")

    notary_repo = Path(os.environ.get("NOTARY_REPO", "/tmp/notary-memory-kit-notary"))
    sys.path.insert(0, str(notary_repo))
    from benchmark.scoring import governance_score, provenance_coverage, stability_score

    governance, governance_issues = governance_score(facts)
    stability, stability_issues = stability_score(facts, authorities)
    provenance = provenance_coverage(facts)

    if governance != 1.0:
        fail(f"expected governance 1.0, got {governance}: {governance_issues}")
    if provenance != 1.0:
        fail(f"expected provenance 1.0, got {provenance}")
    if stability >= 1.0:
        fail(f"expected stability issue, got {stability}: {stability_issues}")
    if not any(unauthorized_fact_id in issue and "overwrite not permitted" in issue for issue in stability_issues):
        fail(f"expected {unauthorized_fact_id} unauthorized overwrite issue, got {stability_issues}")

    print("PASS: demo flow fixture")


def assert_validation_issue(label: str, issues: list[str], expected: str) -> None:
    if not any(expected in issue for issue in issues):
        fail(f"{label}: expected {expected!r}, got {issues}")


def run_negative_validation_tests() -> None:
    valid_fact = {
        "fact_id": "neg-f001",
        "content": "Synthetic validation fixture.",
        "agent_id": "planner",
        "session_id": "neg-s001",
        "timestamp": "2026-01-01T00:00:00Z",
        "surface": "project_scope",
        "lifecycle": "permanent",
        "confidence": 1.0,
    }

    duplicate_issues = validate_facts([valid_fact, dict(valid_fact)])
    assert_validation_issue("duplicate fact_id", duplicate_issues, "duplicate fact_id")

    bad_timestamp = dict(valid_fact)
    bad_timestamp["fact_id"] = "neg-f002"
    bad_timestamp["timestamp"] = "2026/01/01"
    timestamp_issues = validate_facts([bad_timestamp])
    assert_validation_issue("bad timestamp", timestamp_issues, "invalid timestamp")

    bad_confidence = dict(valid_fact)
    bad_confidence["fact_id"] = "neg-f003"
    bad_confidence["confidence"] = "high"
    confidence_issues = validate_facts([bad_confidence])
    assert_validation_issue("bad confidence", confidence_issues, "invalid confidence")

    missing_authority = validate_authorities([], [valid_fact])
    assert_validation_issue("missing authority", missing_authority, "missing WriteAuthority")

    bad_authority = {
        "agent_id": "planner",
        "allowed_surfaces": "project_scope",
        "can_overwrite": "yes",
    }
    authority_issues = validate_authorities([bad_authority], [valid_fact])
    assert_validation_issue("bad allowed_surfaces", authority_issues, "invalid allowed_surfaces")
    assert_validation_issue("bad can_overwrite", authority_issues, "can_overwrite must be boolean")

    print("PASS: negative validation fixtures")


if __name__ == "__main__":
    run_negative_validation_tests()
    main()
