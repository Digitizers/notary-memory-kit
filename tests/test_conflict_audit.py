"""Standalone tests for the cross-agent conflict / poisoning audit (stdlib only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notary_memory_kit.cli import audit_cross_agent_conflicts  # noqa: E402


def fact(**overrides):
    base = {
        "fact_id": "f001",
        "content": "Fact content.",
        "agent_id": "agent-a",
        "session_id": "sess-001",
        "timestamp": "2026-01-01T00:00:00Z",
        "surface": "project_scope",
        "lifecycle": "permanent",
        "confidence": 1.0,
        "overwrite_of": None,
    }
    base.update(overrides)
    return base


AUTHORITIES = [
    {"agent_id": "agent-a", "allowed_surfaces": ["project_scope"], "can_overwrite": False},
    {
        "agent_id": "agent-b",
        "allowed_surfaces": ["project_scope"],
        "can_overwrite": True,
        "max_confidence_claim": 0.9,
    },
]


def test_unauthorized_cross_agent_overwrite_is_flagged() -> None:
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-b"),
        fact(fact_id="f002", agent_id="agent-a", overwrite_of="f001"),
    ])

    assert any("unresolved cross-agent conflict" in warning for warning in warnings), warnings
    print("PASS: unauthorized cross-agent overwrite flagged")


def test_authorized_cross_agent_overwrite_is_clean() -> None:
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-a", confidence=0.8),
        fact(fact_id="f002", agent_id="agent-b", confidence=0.9, overwrite_of="f001"),
    ])

    assert warnings == [], warnings
    print("PASS: authorized cross-agent overwrite clean")


def test_cross_agent_confidence_dilution_is_flagged_even_when_authorized() -> None:
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-a", confidence=0.95),
        fact(fact_id="f002", agent_id="agent-b", confidence=0.5, overwrite_of="f001"),
    ])

    assert any("cross-agent confidence dilution" in warning for warning in warnings), warnings
    assert not any("unresolved" in warning for warning in warnings), warnings
    print("PASS: dilution flagged for authorized takeover")


def test_confidence_above_declared_ceiling_is_flagged() -> None:
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-b", confidence=0.99),
    ])

    assert any("confidence inflation" in warning for warning in warnings), warnings
    print("PASS: confidence inflation flagged")


def test_numeric_string_confidence_is_audited_like_the_validator_accepts_it() -> None:
    # validate_facts coerces confidence with float(...), so numeric strings
    # are valid evidence — the audit must not silently skip them.
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-b", confidence="0.99"),
        fact(fact_id="f002", agent_id="agent-a", confidence="0.95"),
        fact(fact_id="f003", agent_id="agent-b", confidence="0.5", overwrite_of="f002"),
    ])

    assert any("confidence inflation" in warning for warning in warnings), warnings
    assert any("cross-agent confidence dilution" in warning for warning in warnings), warnings
    print("PASS: numeric-string confidence audited")


def test_same_agent_overwrite_and_missing_ceiling_are_clean() -> None:
    warnings = audit_cross_agent_conflicts(AUTHORITIES, [
        fact(fact_id="f001", agent_id="agent-a", confidence=1.0),
        fact(fact_id="f002", agent_id="agent-a", confidence=0.4, overwrite_of="f001"),
    ])

    assert warnings == [], warnings
    print("PASS: same-agent correction and missing ceiling clean")


def run_all() -> None:
    test_unauthorized_cross_agent_overwrite_is_flagged()
    test_authorized_cross_agent_overwrite_is_clean()
    test_cross_agent_confidence_dilution_is_flagged_even_when_authorized()
    test_confidence_above_declared_ceiling_is_flagged()
    test_numeric_string_confidence_is_audited_like_the_validator_accepts_it()
    test_same_agent_overwrite_and_missing_ceiling_are_clean()
    print("PASS: conflict audit tests")


if __name__ == "__main__":
    run_all()
