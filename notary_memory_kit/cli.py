from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path
from typing import Any


STORE_DIR = ".notary-memory-kit"
STORE_FILE = "store.json"
REQUIRED_FACT_FIELDS = {
    "fact_id",
    "content",
    "agent_id",
    "session_id",
    "timestamp",
    "surface",
    "lifecycle",
    "confidence",
}


def default_store_path(root: Path) -> Path:
    return root / STORE_DIR / STORE_FILE


def parse_value(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() == "null":
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass
    if "," in value:
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def evidence_path(root: Path, path: Path) -> str:
    return str(path.relative_to(root))


def parse_markdown_fact(root: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing front matter")

    _, front, body = text.split("---\n", 2)
    fact: dict[str, Any] = {}
    for line in front.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        fact[key.strip()] = parse_value(raw_value)

    content = body.strip()
    if content.startswith("#"):
        content_lines = [line for line in content.splitlines() if not line.startswith("#")]
        content = "\n".join(content_lines).strip()
    fact.setdefault("content", content)
    fact.setdefault("evidence_path", evidence_path(root, path))
    return fact


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_facts(root: Path) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    for path in sorted((root / "logs").glob("*.md")):
        facts.append(parse_markdown_fact(root, path))

    for path in sorted((root / "sessions").glob("*.json")):
        data = read_json(path)
        for fact in data.get("facts", []):
            fact.setdefault("evidence_path", evidence_path(root, path))
            facts.append(fact)

    return facts


def load_authorities(root: Path) -> list[dict[str, Any]]:
    policy = root / "policy" / "write-authority.json"
    if not policy.exists():
        return []
    data = read_json(policy)
    return data.get("authorities", [])


def validate_facts(facts: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    valid_lifecycles = {"permanent", "session", "volatile"}

    seen_fact_ids: set[str] = set()
    for fact in facts:
        fact_id = fact.get("fact_id", "<unknown>")
        if fact_id in seen_fact_ids:
            issues.append(f"[{fact_id}] duplicate fact_id")
        seen_fact_ids.add(fact_id)

        missing = sorted(field for field in REQUIRED_FACT_FIELDS if field not in fact)
        for field in missing:
            issues.append(f"[{fact_id}] missing {field}")

        lifecycle = fact.get("lifecycle")
        if lifecycle is not None and lifecycle not in valid_lifecycles:
            issues.append(f"[{fact_id}] invalid lifecycle {lifecycle!r}")

        timestamp = fact.get("timestamp")
        if timestamp and not is_isoish_timestamp(str(timestamp)):
            issues.append(f"[{fact_id}] invalid timestamp {timestamp!r}")

        confidence = fact.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool):
                # bool is an int subclass, so float(True) == 1.0 would pass —
                # but a boolean is not a confidence value. Rejecting it here
                # keeps validation aligned with the conflict audit's coercion.
                issues.append(f"[{fact_id}] invalid confidence {confidence!r}")
            else:
                try:
                    confidence_value = float(confidence)
                except (TypeError, ValueError):
                    issues.append(f"[{fact_id}] invalid confidence {confidence!r}")
                else:
                    if not (0 <= confidence_value <= 1):
                        issues.append(f"[{fact_id}] confidence out of range: {confidence}")
    return issues


def is_isoish_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


def validate_authorities(authorities: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    seen_agents: set[str] = set()

    for authority in authorities:
        agent_id = authority.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            issues.append("[authority] missing agent_id")
            continue
        if agent_id in seen_agents:
            issues.append(f"[authority:{agent_id}] duplicate authority")
        seen_agents.add(agent_id)

        allowed = authority.get("allowed_surfaces")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) for item in allowed):
            issues.append(f"[authority:{agent_id}] invalid allowed_surfaces")
        if not isinstance(authority.get("can_overwrite"), bool):
            issues.append(f"[authority:{agent_id}] can_overwrite must be boolean")

        ceiling = authority.get("max_confidence_claim")
        if ceiling is not None:
            # A malformed ceiling must fail validation, not silently mean
            # "no ceiling" — that would suppress the audit's
            # confidence-inflation warnings for every fact by this agent.
            ceiling_value = _coerce_confidence(ceiling)
            if ceiling_value is None:
                issues.append(f"[authority:{agent_id}] invalid max_confidence_claim {ceiling!r}")
            elif not (0 <= ceiling_value <= 1):
                issues.append(f"[authority:{agent_id}] max_confidence_claim out of range: {ceiling}")

    known_agents = {authority.get("agent_id") for authority in authorities}
    for fact in facts:
        agent_id = fact.get("agent_id")
        if agent_id and agent_id not in known_agents:
            issues.append(f"[{fact.get('fact_id', '<unknown>')}] missing WriteAuthority for agent '{agent_id}'")

    return issues


def audit_authority_surfaces(authorities: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    auth_map = {authority.get("agent_id"): authority for authority in authorities}

    for fact in facts:
        agent_id = fact.get("agent_id")
        surface = fact.get("surface")
        fact_id = fact.get("fact_id", "<unknown>")
        if not agent_id or not surface:
            continue

        authority = auth_map.get(agent_id)
        if not authority:
            continue

        allowed = authority.get("allowed_surfaces", [])
        if surface not in allowed:
            issues.append(f"[{fact_id}] agent '{agent_id}' is not authorized for surface '{surface}'")

    return issues


def _coerce_confidence(value: Any) -> float | None:
    """Coerce a confidence value the same way validate_facts accepts it.

    The schema allows numeric strings; the audit must not silently skip
    values the validator declared valid.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def audit_cross_agent_conflicts(authorities: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[str]:
    """Warnings for cross-agent conflicts and poisoning signals the
    evidence can prove.

    - An overwrite whose in-set target was written by a DIFFERENT agent
      is a cross-agent conflict; it is only clean when the overwriting
      agent holds can_overwrite and the surface.
    - A cross-agent overwrite that lowers the target's confidence is a
      dilution signal even when authorized.
    - A fact whose confidence exceeds its agent's max_confidence_claim
      (when the authority declares one) is confidence inflation.

    Warnings only, matching the authority audit: the kit prepares
    evidence for review, it does not block ingestion.
    """
    issues: list[str] = []
    auth_map = {authority.get("agent_id"): authority for authority in authorities}
    facts_by_id = {fact.get("fact_id"): fact for fact in facts if fact.get("fact_id")}

    for fact in facts:
        fact_id = fact.get("fact_id", "<unknown>")
        agent_id = fact.get("agent_id")
        if not agent_id:
            continue

        authority = auth_map.get(agent_id)
        ceiling = _coerce_confidence((authority or {}).get("max_confidence_claim"))
        confidence = _coerce_confidence(fact.get("confidence"))
        if ceiling is not None and confidence is not None and confidence > ceiling:
            issues.append(
                f"[{fact_id}] agent '{agent_id}' confidence {confidence}"
                f" above max_confidence_claim {ceiling} — confidence inflation"
            )

        target = facts_by_id.get(fact.get("overwrite_of"))
        if not target or target is fact:
            continue
        target_agent = target.get("agent_id")
        if not target_agent or target_agent == agent_id:
            continue

        # Authority must cover the surface of the fact being displaced as
        # well — relabeling the replacement to an allowed surface must not
        # launder a takeover of memory the agent has no authority over.
        allowed_surfaces = (authority or {}).get("allowed_surfaces", [])
        authorized = bool(
            authority
            and authority.get("can_overwrite")
            and fact.get("surface") in allowed_surfaces
            and target.get("surface") in allowed_surfaces
        )
        if not authorized:
            issues.append(
                f"[{fact_id}] agent '{agent_id}' overwrites fact"
                f" '{target.get('fact_id')}' by agent '{target_agent}'"
                " without authority — unresolved cross-agent conflict"
            )

        target_confidence = _coerce_confidence(target.get("confidence"))
        if (
            confidence is not None
            and target_confidence is not None
            and confidence < target_confidence
        ):
            issues.append(
                f"[{fact_id}] agent '{agent_id}' lowers confidence of fact"
                f" '{target.get('fact_id')}' by agent '{target_agent}'"
                f" ({target_confidence} -> {confidence}) — cross-agent confidence dilution"
            )

    return issues


def load_store(target: Path) -> dict[str, Any]:
    if target.is_dir():
        path = default_store_path(target)
    else:
        path = target
    return read_json(path)


def cmd_ingest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    facts = load_facts(root)
    authorities = load_authorities(root)
    issues = validate_facts(facts) + validate_authorities(authorities, facts)
    if issues:
        print("Validation failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    store = {
        "source_root": root.name,
        "facts": facts,
        "authorities": authorities,
        "authority_audit": audit_authority_surfaces(authorities, facts),
        "conflict_audit": audit_cross_agent_conflicts(authorities, facts),
    }
    store_path = Path(args.store) if args.store else default_store_path(root)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    audit_count = len(store["authority_audit"])
    conflict_count = len(store["conflict_audit"])
    print(f"Ingested {len(facts)} facts and {len(authorities)} authorities -> {store_path}")
    if audit_count:
        print(f"Authority audit warnings: {audit_count}")
    if conflict_count:
        print(f"Conflict audit warnings: {conflict_count}")
    return 0


def cmd_facts(args: argparse.Namespace) -> int:
    store = load_store(Path(args.target).resolve())
    facts = store.get("facts", [])
    for fact in facts:
        print(
            f"{fact.get('fact_id')} | {fact.get('agent_id')} | "
            f"{fact.get('lifecycle')} | {fact.get('surface')} | {fact.get('content')}"
        )
    print(f"\n{len(facts)} facts")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = load_store(Path(args.target).resolve())
    query = args.query.lower()
    matches = [
        fact for fact in store.get("facts", [])
        if query in fact.get("content", "").lower()
        or query in " ".join(fact.get("tags", [])).lower()
    ]
    for fact in matches:
        print(f"{fact.get('fact_id')} | {fact.get('content')}")
    print(f"\n{len(matches)} matches")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = load_store(Path(args.target).resolve())
    output = Path(args.notary).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    export = {
        "facts": store.get("facts", []),
        "authorities": store.get("authorities", []),
        "authority_audit": store.get("authority_audit", []),
        "conflict_audit": store.get("conflict_audit", []),
    }
    output.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(export['facts'])} facts -> {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notary-memory-kit")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("root")
    ingest.add_argument("--store")
    ingest.set_defaults(func=cmd_ingest)

    facts = sub.add_parser("facts")
    facts.add_argument("target")
    facts.set_defaults(func=cmd_facts)

    search = sub.add_parser("search")
    search.add_argument("target")
    search.add_argument("query")
    search.set_defaults(func=cmd_search)

    export = sub.add_parser("export")
    export.add_argument("target")
    export.add_argument("--notary", required=True)
    export.set_defaults(func=cmd_export)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
