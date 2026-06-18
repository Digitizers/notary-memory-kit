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
    }
    store_path = Path(args.store) if args.store else default_store_path(root)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    audit_count = len(store["authority_audit"])
    print(f"Ingested {len(facts)} facts and {len(authorities)} authorities -> {store_path}")
    if audit_count:
        print(f"Authority audit warnings: {audit_count}")
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
